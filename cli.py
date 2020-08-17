"""Command line interface to the OSF

These functions implement the functionality of the command-line interface.
"""
from __future__ import print_function

from functools import wraps
import getpass
import os
import sys

from six.moves import configparser
from six.moves import input

from tqdm import tqdm

from .api import OSF
from .exceptions import UnauthorizedException
from .utils import norm_remote_path, split_storage, makedirs


def config_from_file():
    if os.path.exists(".osfcli.config"):
        config_ = configparser.ConfigParser()
        config_.read(".osfcli.config")

        # for python2 compatibility
        config = dict(config_.items('osf'))

    else:
        config = {}

    return config


def config_from_env(config):
    username = os.getenv("OSF_USERNAME")
    if username is not None:
        config['username'] = username

    project = os.getenv("OSF_PROJECT")
    if project is not None:
        config['project'] = project

    return config


def _get_username(args, config):
    if args.username is None:
        username = config.get('username')
    else:
        username = args.username
    return username


def _setup_osf(args):
    # Command line options have precedence over environment variables,
    # which have precedence over the config file.
    config = config_from_env(config_from_file())

    username = _get_username(args, config)

    project = config.get('project')
    if args.project is None:
        args.project = project
    # still None? We are in trouble
    if args.project is None:
        sys.exit('You have to specify a project ID via the command line,'
                 ' configuration file or environment variable.')

    password = None
    if username is not None:
        password = os.getenv("OSF_PASSWORD")

        # Prompt user when password is not set
        if password is None:
            password = getpass.getpass('Please input your password: ')

    return OSF(username=username, password=password)


def might_need_auth(f):
    """Decorate a CLI function that might require authentication.

    Catches any UnauthorizedException raised, prints a helpful message and
    then exits.
    """
    @wraps(f)
    def wrapper(cli_args):
        try:
            return_value = f(cli_args)
        except UnauthorizedException as e:
            config = config_from_env(config_from_file())
            username = _get_username(cli_args, config)

            if username is None:
                sys.exit("Please set a username (run `osf -h` for details).")
            else:
                sys.exit("You are not authorized to access this project.")

        return return_value

    return wrapper


def init(args):
    """Initialize or edit an existing .osfcli.config file."""
    # reading existing config file, convert to configparser object
    config = config_from_file()
    config_ = configparser.ConfigParser()
    config_.add_section('osf')
    if 'username' not in config.keys():
        config_.set('osf', 'username', '')
    else:
        config_.set('osf', 'username', config['username'])
    if 'project' not in config.keys():
        config_.set('osf', 'project', '')
    else:
        config_.set('osf', 'project', config['project'])

    # now we can start asking for new values
    print('Provide a username for the config file [current username: {}]:'.format(
          config_.get('osf', 'username')))
    username = input()
    if username:
        config_.set('osf', 'username', username)

    print('Provide a project for the config file [current project: {}]:'.format(
          config_.get('osf', 'project')))
    project = input()
    if project:
        config_.set('osf', 'project', project)

    cfgfile = open(".osfcli.config", "w")
    config_.write(cfgfile)
    cfgfile.close()


@might_need_auth
def clone(args):
    """Copy all files from all storages of a project.

    The output directory defaults to the current directory.

    If the project is private you need to specify a username.
    """
    osf = _setup_osf(args)
    project = osf.project(args.project)
    output_dir = args.project
    if args.output is not None:
        output_dir = args.output

    with tqdm(unit='files') as pbar:
        for store in project.storages:
            prefix = os.path.join(output_dir, store.name)
            prefix = prefix.replace(os.sep,'/')
            for file_ in store.files:
                path = file_.path
                path = path.replace(os.sep,'/')
                if path.startswith('/'):
                    path = path[1:]

                path = os.path.join(prefix, path)
                path = path.replace(os.sep,'/')
                directory, _ = os.path.split(path)
                directory = directory.replace(os.sep,'/')
                makedirs(directory, exist_ok=True)

                with open(path, "wb") as f:
                    file_.write_to(f)

                pbar.update()


@might_need_auth
def fetch(args):
    """Fetch an individual file from a project.

    The first part of the remote path is interpreted as the name of the
    storage provider. If there is no match the default (osfstorage) is
    used.

    The local path defaults to the name of the remote file.

    If the project is private you need to specify a username.
    """
    storage, remote_path = split_storage(args.remote)

    local_path = args.local
    local_path = local_path.replace(os.sep,'/')
    if local_path is None:
        _, local_path = os.path.split(remote_path)
        local_path = local_path.replace(os.sep,'/')

    if os.path.exists(local_path) and not args.force:
        sys.exit("Local file %s already exists, not overwriting." % local_path)

    directory, _ = os.path.split(local_path)
    directory = directory.replace(os.sep,'/')
    if directory:
        makedirs(directory, exist_ok=True)

    osf = _setup_osf(args)
    project = osf.project(args.project)

    store = project.storage(storage)
    for file_ in store.files:
        if norm_remote_path(file_.path) == remote_path:
            with open(local_path, 'wb') as fp:
                file_.write_to(fp)

            # only fetching one file so we are done
            break


@might_need_auth
def list_(args):
    """List all files from all storages for project.

    If the project is private you need to specify a username.
    """
    osf = _setup_osf(args)

    project = osf.project(args.project)

    for store in project.storages:
        prefix = store.name
        for file_ in store.files:
            path = file_.path
            path = path.replace(os.sep,'/')
            if path.startswith('/'):
                path = path[1:]

            print(os.path.join(prefix, path).replace(os.sep,'/'))


@might_need_auth
def upload(args):
    """Upload a new file to an existing project.

    The first part of the remote path is interpreted as the name of the
    storage provider. If there is no match the default (osfstorage) is
    used.

    If the project is private you need to specify a username.

    To upload a whole directory (and all its sub-directories) use the `-r`
    command-line option. If your source directory name ends in a / then
    files will be created directly in the remote directory. If it does not
    end in a slash an extra sub-directory with the name of the local directory
    will be created.

    To place contents of local directory `foo` in remote directory `bar/foo`:
    $ osf upload -r foo bar
    To place contents of local directory `foo` in remote directory `bar`:
    $ osf upload -r foo/ bar
    """
    osf = _setup_osf(args)
    if osf.username is None or osf.password is None:
        sys.exit('To upload a file you need to provide a username and'
                 ' password.')

    project = osf.project(args.project)
    storage, remote_path = split_storage(args.destination)

    store = project.storage(storage)
    if args.recursive:
        if not os.path.isdir(args.source):
            raise RuntimeError("Expected source ({}) to be a directory when "
                               "using recursive mode.".format(args.source))

        # local name of the directory that is being uploaded
        _, dir_name = os.path.split(args.source)

        for root, _, files in os.walk(args.source):
            # these are extra subdirectories we have walked into since the root
            # directory, have to clean off leading slashes from their name
            # for path.join() to work later on
            subdir_path = root.replace(args.source, '')
            subdir_path = subdir_path.replace(os.sep,'/')
            if subdir_path.startswith('/'):
                subdir_path = subdir_path[1:]

            for fname in files:
                local_path = os.path.join(root, fname)
                local_path = local_path.replace(os.sep,'/')
                with open(local_path, 'rb') as fp:
                    # build the remote path + fname
                    name = os.path.join(remote_path, dir_name, subdir_path,
                                        fname)
                    store.create_file(name, fp, update=args.force)

    else:
        if remote_path == '.' :
            _ , remote_path = os.path.split(args.source)
        with open(args.source, 'rb') as fp:
            store.create_file(remote_path, fp, update=args.force)


@might_need_auth
def remove(args):
    """Remove a file from the project's storage.

    The first part of the remote path is interpreted as the name of the
    storage provider. If there is no match the default (osfstorage) is
    used.
    """
    osf = _setup_osf(args)
    if osf.username is None or osf.password is None:
        sys.exit('To remove a file you need to provide a username and'
                 ' password.')

    project = osf.project(args.project)

    storage, remote_path = split_storage(args.target)
    remote_path = remote_path.replace(os.sep,'/')
    store = project.storage(storage)
    for f in store.files:
        if norm_remote_path(f.path) == remote_path:
            f.remove()

# The Craftr build system
# Copyright (C) 2016  Niklas Rosenstein
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

from craftr import core
from craftr.core.logging import logger
from craftr.core.session import session, Session, Module
from craftr.utils import path, shell
from nr.types.version import Version, VersionCriteria

import abc
import argparse
import atexit
import configparser
import craftr
import json
import os
import sys
import textwrap


def write_cache(cachefile):
  # Write back the cache.
  try:
    path.makedirs(path.dirname(cachefile))
    with open(cachefile, 'w') as fp:
      session.write_cache(fp)
  except OSError as exc:
    logger.error('error writing cache file:', cachefile)
    logger.error(exc, indent=1)
  else:
    logger.debug('cache written:', cachefile)


def read_config_file(filename):
  """
  Parse a configuration file and return a dictionary that contains the
  option keys concatenated with the section names separated by a dot.
  """

  filename = path.norm(filename)
  if not path.isfile(filename):
    return {}
  logger.debug('reading configuration file:', filename)
  parser = configparser.SafeConfigParser()
  parser.read([filename])
  result = {}
  for section in parser.sections():
    for option in parser.options(section):
      result['{}.{}'.format(section, option)] = parser.get(section, option)
  return result


def parse_module_spec(spec):
  """
  Parses a module spec as it can be specified on the command-line. The
  format is ``module_name[:version]``.
  """

  parts = spec.split(':')
  if len(parts) not in (1, 2):
    raise ValueError('invalid module spec: {!r}'.format(spec))
  if len(parts) == 1:
    parts.append('*')
  try:
    version = Version(parts[1])
  except ValueError as exc:
    version = VersionCriteria(parts[1])
  return parts[0], version


def get_volatile_module_version(name):
  """
  Given a module name of which the exact version number may be appended
  and separated by a hyphen, returns the raw module name and its version
  as a tuple. If the module is not suffixed with a version number, the
  returned version number is :const:`None` instead.
  """

  parts = name.rpartition('-')[::2]
  if len(parts) != 2:
    return name
  try:
    version = Version(parts[1])
  except ValueError:
    version = None
  else:
    name = parts[0]
  return name, version


class BaseCommand(object, metaclass=abc.ABCMeta):
  """
  Base class for Craftr subcommands.
  """

  @abc.abstractmethod
  def build_parser(self, parser):
    pass

  @abc.abstractmethod
  def execute(self, parser, args):
    pass


class build(BaseCommand):

  def build_parser(self, parser):
    parser.add_argument('targets', metavar='TARGET', nargs='*')
    parser.add_argument('-m', '--module')
    parser.add_argument('-b', '--build-dir', default='build')
    parser.add_argument('-c', '--config', default='.craftrconfig')
    parser.add_argument('-d', '--option', dest='options', action='append', default=[])

  def execute(self, parser, args):
    # Determine the module to execute, either from the current working
    # directory or find it by name if one is specified.
    if not args.module:
      for fn in ['manifest.json', 'craftr/manifest.json']:
        if path.isfile(fn):
          module = session.parse_manifest(fn)
          break
      else:
        parser.error('no Craftr package "manifest.json" found')
    else:
      # TODO: For some reason, prints to stdout are not visible here.
      # TODO: Prints to stderr however work fine.
      try:
        module_name, version = parse_module_spec(args.module)
      except ValueError as exc:
        parser.error('{} (note: you have to escape > and < characters)'.format(exc))
      try:
        module = session.find_module(module_name, version)
      except Module.NotFound as exc:
        parser.error('module not found: ' + str(exc))

    session.options.update(read_config_file(args.config))
    for item in args.options:
      key, sep, value = item.partition('=')
      if not sep:
        value = 'true'
      elif not value:
        session.options.pop(key, None)
      else:
        session.options[key] = value

    # Create and switch to the build directory.
    session.builddir = path.abs(args.build_dir)
    path.makedirs(session.builddir)
    os.chdir(session.builddir)

    # Read the cache.
    cachefile = path.join(session.builddir, '.craftrcache')
    if path.isfile(cachefile):
      with open(cachefile) as fp:
        try:
          session.read_cache(fp)
        except ValueError as exc:
          logger.error('error reading cache file:', cachefile)
          logger.error(exc, indent=1)

    # Prepare options, loaders and execute.
    try:
      logger.info('==> initializing options')
      with logger.indent():
        module.init_options(True)

      logger.info('==> initializing loaders')
      with logger.indent():
        module.init_loader(True)

      write_cache(cachefile)
      logger.info('==> executing build script')
      with logger.indent():
        module.run()
    except (Module.InvalidOption, Module.LoaderInitializationError) as exc:
      for error in exc.format_errors():
        logger.error(error)
      return 1
    except craftr.defaults.ModuleError as exc:
      logger.error(exc)
      return 1

    # Write the cache back.
    write_cache(cachefile)

    # Check the targets and if they exist.
    targets = []
    for target in args.targets:
      if '.' not in target:
        target = module.manifest.name + '.' + target
        version = module.manifest.version
      elif target.startswith('.'):
        target = module.manifest.name + target
        version = module.manifest.version
      else:
        version = None

      module_name, target = target.rpartition('.')[::2]
      if not version:
        module_name, version = get_volatile_module_version(module_name)
      ref_module = session.find_module(module_name, version or '*')
      target = craftr.targetbuilder.get_full_name(target, ref_module)
      if target not in session.graph.targets:
        parser.error('no such target: {}'.format(target))
      targets.append(target)

    # Write the Ninja manifest.
    with open("build.ninja", 'w') as fp:
      platform = core.build.get_platform_helper()
      context = core.build.ExportContext('1.7.1')  # TODO: Get ninja version
      writer = core.build.NinjaWriter(fp)
      session.graph.export(writer, context, platform)

    # Execute the ninja build.
    shell.run(['ninja'] + targets)

class startpackage(BaseCommand):

  def build_parser(self, parser):
    parser.add_argument('name')
    parser.add_argument('directory', nargs='?', default=None)
    parser.add_argument('--version', type=Version, default='1.0.0')

  def execute(self, parser, args):
    directory = args.directory or args.name

    if not path.exists(directory):
      logger.debug('creating directory "{}"'.format(directory))
      path.makedirs(directory)
    elif not path.isdir(directory):
      logger.error('"{}" is not a directory'.format(directory))
      return 1

    mfile = path.join(directory, 'manifest.json')
    sfile = path.join(directory, 'Craftrfile')
    for fn in [mfile, sfile]:
      if path.isfile(fn):
        logger.error('"{}" already exists'.format(fn))
        return 1

    logger.debug('creating file "{}"'.format(mfile))
    with open(mfile, 'w') as fp:
      fp.write(textwrap.dedent('''
        {
          "name": "%s",
          "version": "%s",
          "author": "",
          "url": "",
          "dependencies": {},
          "options": {},
          "loaders": []
        }\n''' % (args.name, args.version)).lstrip())

    logger.debug('creating file "{}"'.format(sfile))
    with open(sfile, 'w') as fp:
      print('# {}'.format(args.name), file=fp)


def main():
  # Create argument parsers and dynamically include all BaseCommand
  # subclasses into it.
  parser = argparse.ArgumentParser(prog='craftr', description='The Craftr build system')
  parser.add_argument('-v', dest='verbose', action='count', default=0)
  subparsers = parser.add_subparsers(dest='command')

  commands = {}
  for class_ in BaseCommand.__subclasses__():
    cmd = class_()
    cmd.build_parser(subparsers.add_parser(class_.__name__))
    commands[class_.__name__] = cmd

  # Parse the arguments.
  args = parser.parse_args()
  if not args.command:
    parser.print_usage()
    return 0

  if args.verbose:
    logger.set_level(logger.DEBUG)

  session = Session()

  # Parse the user configuration file.
  session.options = read_config_file(path.expanduser('~/.craftrconfig'))

  # Execute the command in the session context.
  with session:
    return commands[args.command].execute(parser, args)


def main_and_exit():
  sys.exit(main())


if __name__ == '__main__':
  main_and_exit()

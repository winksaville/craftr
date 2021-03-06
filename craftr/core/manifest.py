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
"""
:mod:`craftr.core.manifest`
===========================

This module implemenets parsing and representing the manifest files of
Craftr packages. Every Craftr package also provides a Craftr module which
can be loaded with the ``require()`` function.

Example manifest:

.. code:: json

  {
    "name": "username.packagename",
    "version": "1.0.3",
    "main": "Craftrfile",
    "author": "User Name <username@nameuser.org>",
    "url": "https://github.com/username/packagename",
    "dependencies": {
      "another_user.another_package": "1.x"
    },
    "options": {
      "SOURCE_DIR": {"type": "string"},
      "VERSION": {"type": "string"}
    },
    "loaders": [
      {
        "name": "source",
        "type": "url",
        "urls": [
          "$SOURCE_DIR",
          "http://some-mirror.org/ftp/packagename/tree/$VERSION.tar.gz"
        ]
      }
    ]
  }
"""

from craftr.core.logging import logger
from craftr.utils import httputils
from craftr.utils import path
from craftr.utils import pyutils
from nr.types.recordclass import recordclass
from nr.types.version import Version, VersionCriteria

import abc
import fnmatch
import io
import json
import jsonschema
import nr.misc.archive
import re
import string
import urllib.request


def validate_package_name(name):
  """
  Validates the package *name*. A valid package name must consist only
  of letters, digits, hyphes, underscores and periods. The first character
  of a package must be a letter or digit.

  :raise ValueError: If *name* is not a valid package name.
  """

  if not isinstance(name, str):
    raise TypeError("package name must be str but got {!r}".format(tn(name)))
  if not re.match(r'^[A-z0-9][A-z0-9\.\-\_]*$', name):
    raise ValueError("invalid package name: {!r}".format(name))


class Namespace(object):
  """
  An empty class which is used to assign arbitrary attributes to. An instance
  of this class is created by :meth:`Manifest.get_options_namespace`.
  """

  def __str__(self):
    attrs = ', '.join('{}={!r}'.format(k, v) for k, v in vars(self).items())
    return 'Namespace({})'.format(attrs)


class InvalidManifest(Exception):
  """
  This exception can be raised by :meth:`Manifest.parse`
  """


class Manifest(recordclass):
  """
  Represents the manifest of a Craftr package. The manifest contains the basic
  information about the package name, version and its dependencies, as well as
  its author and homepage URL.

  .. attribute:: name

    The name of the Craftr package.

  .. attribute:: version

    The version of the Craftr package. This must be a semantic version
    number parsable by the :class:`Version` class.

  .. attribute:: main

    The name of the main build script that is executed for the package.
    If this member is None, Craftr will interpret it as the default value
    specified in the :class:`craftr.core.session.Session`.

  .. attribute:: project_dir

    Relative path to the project directory. Local paths in the build script
    are automatically assumed relative to this directory. Defaults to ``..``.

  .. attribute:: author

    Name of the package author.

  .. attribute:: url

    The homepage of the project of this package.

  .. attribute:: dependencies

    A dictionary that maps package names to version critiera parsable by
    the :class:`VersionCriteria` class. The criteria is very similar to
    Node.js version selectors.

  .. attribute:: options

    A dictionary of options that can be provided by the user before
    Craftr is being executed. The option name maps to a :class:`BaseOption`
    instance.

  .. attribute:: loaders

    A list of zero or more :class:`BaseLoader` objects that will be used
    by Craftr to load additional requirements of a module such as source
    files or prebuilt binaries.
  """

  Schema = {
    "type": "object",
    "required": ["name", "version"],
    "properties": {
      "name": {"type": "string"},
      "version": {"type": "string"},
      "main": {"type": "string"},
      "project_dir": {"type": "string"},
      "author": {"type": "string"},
      "url": {"type": "string"},
      "dependencies": {
        "type": "object",
        "additionalProperties": {"type": "string"}
      },
      "options": {
        "type": "object",
        "additionalProperties": {
          "type": "object",
          "properties": {
            "type": {"type": "string"}
          },
          "additionalProperties": True
        }
      },
      "loaders": {
        "type": "array",
        "items": {
          "type": "object",
          "properties": {
            "name": {"type": "string"},
            "type": {"type": "string"}
          },
          "additionalProperties": True
        }
      }
    }
  }

  Invalid = InvalidManifest

  __slots__ = tuple(Schema['properties'].keys())

  def __init__(self, name, version, main='Craftrfile', project_dir='.',
               author=None, url=None, dependencies=None, options=None,
               loaders=None):
    if version is not None:
      version = Version(version)
    self.name = name
    self.version = version
    self.main = main
    self.project_dir = project_dir
    self.author = author
    self.url = url
    self.dependencies = dependencies or {}
    self.options = options or {}
    self.loaders = loaders or []

  def get_options_namespace(self, provider, errors=None):
    """
    Create a :class:`Namespace` object filled with the option values specified
    in the manifest where the option values are read from *provider*.

    :param provider: A dictionary that provides option values.
    :param errors: A list to which tuples of error information will be
      appended. The tuples are in the format (option, value, exc).
    :return Namespace
    """

    if errors is None:
      errors = []
    ns = Namespace()
    for key, option in self.options.items():
      value = provider.get(self.name + '.' + option.name, NotImplemented)
      if value is NotImplemented and option.inherit:
        value = provider.get(option.name)
      if value is NotImplemented or value is None:
        value = option.default
      else:
        try:
          value = option(value)
        except ValueError as exc:
          errors.append((option, value, exc))
          value = option.default
      setattr(ns, key, value)
    return ns

  @staticmethod
  def parse_string(string):
    """
    Shortcut for wrapping *string* in a file-like object and padding it to
    :func:`parse`.
    """

    return Manifest.parse(io.StringIO(string))

  @staticmethod
  def parse(file):
    """
    Parses a manifest file and returns a new :class:`Manifest` object.

    :param file: A filename or file-like object in JSON format.
    :raise InvalidManifest: If the file is not a valid JSON file or the
      manifest data is invalid or inconsistent.
    :return: A :class:`Manifest` object.
    """

    try:
      if isinstance(file, str):
        with open(file) as fp:
          data = json.load(fp)
      else:
        data = json.load(file)
      jsonschema.validate(data, Manifest.Schema)
    except (json.JSONDecodeError, jsonschema.ValidationError) as exc:
      raise Manifest.Invalid(exc)

    try:
      validate_package_name(data['name'])
    except ValueError:
      raise Manifest.Invalid("invalid package name: {!r}".format(data['name']))

    data.setdefault('dependencies', {})
    for key, value in data['dependencies'].items():
      try:
        data['dependencies'][key] = VersionCriteria(value)
      except ValueError as exc:
        msg = 'invalid version critiera for {0!r}: {1!r}'
        raise Manifest.Invalid(msg.format(key, value))

    data.setdefault('options', {})
    for key, value in data['options'].items():
      if isinstance(value, str):
        value = {"type": value}
      type_name = value.pop('type')
      if '.' not in type_name:
        type_name = __name__ + '._aliases.' + type_name
      try:
        option_type = pyutils.import_(type_name)
      except ImportError:
        option_type = None
      if not isinstance(option_type, type) or not issubclass(option_type, BaseOption):
        raise Manifest.Invalid('invalid option type: {!r}'.format(type_name))
      try:
        data['options'][key] = option_type(key, **value)
      except TypeError as exc:
        raise Manifest.Invalid(exc)

    loaders, data['loaders'] = data.pop('loaders', []), []
    taken_loader_names = set()
    for loader_data in loaders:
      name, type_name = loader_data.pop('name'), loader_data.pop('type')
      if name in taken_loader_names:
        raise Manifest.Invalid('duplicate loader name: {!r}'.format(name))
      if '.' not in type_name:
        type_name = __name__ + '._aliases.' + type_name
      try:
        loader_type = pyutils.import_(type_name)
      except ImportError:
        loader_type = None
      if not isinstance(loader_type, type) or not issubclass(loader_type, BaseLoader):
        raise Manifest.Invalid('invalid loader type: {!r}'.format(type_name))
      data['loaders'].append(loader_type(name, **loader_data))

    try:
      return Manifest(**data)
    except TypeError as exc:
      raise Manifest.Invalid(exc)


class BaseOption(object, metaclass=abc.ABCMeta):
  """
  Base class for option value processors that convert and validate options
  from string values (usually provided via the command-line or environment
  variables).

  .. attribute:: name

  .. attribute:: inherit

  .. attribute:: help
  """

  def __init__(self, name, inherit=True, help=None):
    self.name = name
    self.inherit = inherit
    self.help = None

  @abc.abstractmethod
  def __call__(self, value):
    """
    Convert the string *value* to the respective Python representation.
    """


class BoolOption(BaseOption):
  """
  Represents a boolean option. Supports the identifiers "yes", "true",
  "1", "no", "false" and "0".
  """

  def __init__(self, name, default=False, **kwargs):
    super().__init__(name, **kwargs)
    self.default = default

  def __call__(self, value):
    if isinstance(value, str):
      value = value.strip().lower()
      if value in ('yes', 'true', '1'):
        return True
      elif value in ('no', 'false', '0'):
        return False
      elif value == '':
        return self.default
      else:
        raise ValueError("invalid value for bool option: {!r}".format(value))
    else:
      return bool(value)


class TripletOption(BoolOption):
  """
  Just like the :class:`BoolOption` but with a third option, accepting
  "null" and "none" (which maps to :const:`None`).
  """

  def __init__(self, name, default=None, **kwargs):
    super().__init__(name, default, **kwargs)

  def __call__(self, value):
    try:
      return super().__call__(value)
    except ValueError:
      if isinstance(value, str):
        value = value.strip().lower()
        if value in ('null', 'none'):
          return None
      elif value is None:
        return None
    raise ValueError("invalid value for triplet option: {0!r}".format(value))


class StringOption(BaseOption):
  """
  Plain-string option.
  """

  def __init__(self, name, default='', **kwargs):
    super().__init__(name, **kwargs)
    self.default = default

  def __call__(self, string):
    return string


class LoaderError(Exception):
  """
  Raised by :class:`BaseLoader.load`.
  """

  def __init__(self, loader, message):
    self.loader = loader
    self.message = message

  def __str__(self):
    return '{}: {}'.format(self.loader.name, self.message)


class LoaderContext(object):
  """
  The LoaderContext is required for the :class:`BaseLoader.load` method.

  .. attribute:: directory

  .. attribute:: manifest

  .. attribute:: options

  .. attribute:: installdir
  """

  def __init__(self, directory, manifest, options, installdir):
    self.directory = directory
    self.manifest = manifest
    self.options = options
    self.installdir = installdir

  def expand_variables(self, value):
    templ = string.Template(value)
    return templ.safe_substitute(vars(self.options))

  def get_temporary_directory(self):
    """
    Return the path to a temporary directory that a loader can use to
    temporarily store downloaded files.
    """

    raise NotImplementedError


class BaseLoader(object, metaclass=abc.ABCMeta):
  """
  Base class for loader types. Craftr executes the loaders in the order they
  are defined in the :class:`Manifest` until one loader succeeds. This happens
  in the same step in which dependencies are satisfied. In the execute step,
  the loader will be initialized from cache.
  """

  def __init__(self, name):
    self.name = name

  @abc.abstractmethod
  def load(self, context, cache):
    """
    (Down-)load the data. If the process fails, a :class:`LoaderError` should
    be raised. The function must return a dictionary that will be cached in
    the project so that the loader can be initialized with this cache to re-use
    what has been loaded in this method (see :meth:`init_cache`).

    :param context: A :class:`LoaderContext` object.
    :param cache: The cache of a former load operation. This is the data
      returned by a former call to this :meth:`load` method.
    :raise LoaderError: If the loader was unable to load or detect the source.
    :return: A :class:`dict` that will be serialized into a cache.
    """


class UrlLoader(BaseLoader):
  """
  This loader implementation can be used to load source archives from online
  mirrors and automatically unpack them into a directory where they can be
  accessed by the build script, but it can also be used to fall back on
  existing source directories.

  The URLs that are passed to the constructor can contain variables which will
  be expanded given the options of the same manifest. This is usually used to
  allow users to configure the version of the source that is being downloaded
  or to point to an existing directory.

  .. code:: json

    {
      "name": "source",
      "type": "url",
      "urls": [
        "file://$SOURCE_DIR",
        "http://some-mirror.org/ftp/packagename-$SOURCE_VERSION.tar.gz"
      ]
    }

  .. attribute:: urls

  .. attribute:: directory

    If the UrlLoader successfully loaded the source archives or detected the
    source directory, this member is available and points to the directory
    where the sources are located.
  """

  def __init__(self, name, urls, unpack_exclude=()):
    super().__init__(name)
    self.urls = urls
    self.directory = None
    self.unpack_exclude = unpack_exclude

  def load(self, context, cache):
    if cache is not None and path.isdir(cache.get('directory', '')):
      # Check if the requested version changes.
      url_template = context.expand_variables(cache.get('url_template', ''))
      if url_template == cache.get('url'):
        self.directory = cache['directory']
        logger.info('Reusing cached directory: {}'.format(
            path.rel(self.directory, nopar=True)))
        return cache
      else:
        logger.info('Cached URL is outdated:', cache.get('url'))

    directory = None
    archive = None
    delete_after_extract = True
    for url_template in self.urls:
      url = context.expand_variables(url_template)
      if not url: continue
      if url.startswith('file://'):
        name = url[7:]
        if path.isdir(name):
          logger.info('Using directory', url)
          directory = name
          break
        elif path.isfile(name):
          logger.info('Using archive', url)
          archive = name
          delete_after_extract = False
          break
        error = None
      else:
        error = None
        try:
          progress = lambda d: self._download_progress(url, context, d)
          archive, reused = httputils.download_file(
            url, directory = context.get_temporary_directory(),
            on_exists='skip', progress=progress)
        except (httputils.URLError, httputils.HTTPError) as exc:
          error = exc
        except self.DownloadAlreadyExists as exc:
          directory = exc.directory
          logger.info('Reusing existing directory', directory)
        else:
          if reused:
            logger.info('Reusing cached download', path.basename(archive))
          break

      if error:
        logger.info('Error reading', url, ':', error)

    if directory or archive:
      logger.debug('URL applies: {}'.format(url))

    if not directory and archive:
      suffix, directory = self._get_archive_unpack_info(context, archive)
      logger.info('Unpacking "{}" to "{}" ...'.format(
          path.rel(archive, nopar=True), path.rel(directory, nopar=True)))
      nr.misc.archive.extract(archive, directory, suffix=suffix,
          unpack_single_dir=True, check_extract_file=self._check_extract_file,
          progress_callback=self._extract_progress)
    elif not directory:
      raise LoaderError(self, 'no URL matched')

    self.directory = directory
    with open(path.join(self.directory, '.craftr_downloadurl'), 'w') as fp:
      fp.write(url)
    return {'directory': directory, 'url_template': url_template, 'url': url}

  def _download_progress(self, url, context, data):
    spinning = data['size'] is None
    if data['downloaded'] == 0:
      # If what we're trying to download already exists, we don't have
      # to redownload it.
      suffix, directory = self._get_archive_unpack_info(context, data['filename'])
      urlfile = path.join(directory, '.craftr_downloadurl')
      if path.isfile(urlfile):
        with open(urlfile) as fp:
          if fp.read().strip() == url:
            raise self.DownloadAlreadyExists(directory)

      logger.progress_begin('Downloading {}'.format(url), spinning)
    if spinning:
      # TODO: Bytes to human readable
      logger.progress_update(None, data['downloaded'])
    else:
      progress = data['downloaded'] / data['size']
      logger.progress_update(progress, '{}%'.format(int(progress * 100)))
    if data['completed']:
      logger.progress_end()

  def _get_archive_unpack_info(self, context, archive):
    suffix = nr.misc.archive.get_opener(archive)[0]
    filename = path.basename(archive)[:-len(suffix)]
    directory = path.join(context.installdir, filename)
    return suffix, directory

  def _check_extract_file(self, arcname):
    if not self.unpack_exclude:
      return True
    for pattern in self.unpack_exclude:
      if fnmatch.fnmatch(arcname, pattern):
        return False
    return True

  def _extract_progress(self, index, count, filename):
    if index == -1:
      logger.progress_begin(None, True)
      logger.progress_update(0.0, 'Reading index...')
      return
    progress = index / float(count)
    if index == 0:
      logger.progress_end()
      logger.progress_begin(None, False)
    elif index == (count - 1):
      logger.progress_end()
    else:
      logger.progress_update(progress, '{} / {}'.format(index, count))

  class DownloadAlreadyExists(Exception):
    def __init__(self, directory):
      self.directory = directory


class _aliases:
  """
  This class serves as a namespace in order to be able to specify the
  standard option and loader types by their shortcut name in the manifest
  rather than by their FQN.
  """

  bool = BoolOption
  triplet = TripletOption
  string = StringOption

  url = UrlLoader

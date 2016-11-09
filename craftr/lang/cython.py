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
:mod:`craftr.lang.cython`
=========================

This package provides target generators to compile Cython projects.
"""

from craftr.core.session import session
from craftr.defaults import buildlocal, relocate_files
from craftr.lang import python
from craftr.targetbuilder import gtn, Framework, TargetBuilder
from craftr.utils import path
from nr.types.recordclass import recordclass

import functools
import os
import re


class CythonCompiler(object):

  Project = recordclass.new('Project', 'sources main libs alias')

  def __init__(self, program=None):
    if not program:
      program = session.options.get('cython.bin', None)
    if not program:
      program = os.getenv('CYTHON', 'cython')
    self.program = program

  @property
  @functools.lru_cache()
  def version(self):
    output = shell.pipe([self.program, '-V']).output
    match = re.match(r'cython\s+version\s+([\d\.]+)', output, re.I)
    if not match:
      raise ValueError("unable to determine Cython version")
    return match.group(1)

  def compile(self, srcs, py_version=None, outputs=None, outdir='cython/src',
              cpp=False, embed=False, fast_fail=False, include=(),
              additional_flags=(), name=None):
    """
    Compile the Python/Cython source files to C or C++ sources.

    :param srcs: A list of Python/Cython source files.
    :param py_version: The Python version to produce the C/C++ files for.
      Must be ``2`` or ``3``.
    :param outputs: Allows you to override the output C/C++ source output
      for every file in *srcs*.
    :param outdir: Used if *outputs* is not explicitly specified.
    :param cpp: True to translate to C++ source files.
    :param embed: Pass ``--embed`` to Cython. Note that if multiple
      files are specfied in *srcs*, all of them will have a ``int main()``
      function.
    :param fast_fail: True to enable the ``--fast-fail`` flag.
    :param include: Additional include directories for Cython
    :param additional_flags: Additional flags for Cython.
    :param name: The name of the target.

    Build metadata:

    :param cython_outdir: The output directory of the generate source files.
    """

    assert not isinstance(include, str)
    builder = TargetBuilder(gtn(name, "cython"), inputs=srcs)

    outdir = buildlocal(outdir)
    if py_version is None:
      py_version = int(python.get_config()['VERSION'][0])
    if outputs is None:
      outputs = relocate_files(builder.inputs, outdir, '.cpp' if cpp else '.c')
    if py_version not in (2, 3):
      raise ValueError('invalid py_version: {0!r}'.format(py_version))

    command = [self.program, '$in', '-o', '$out', '-' + str(py_version)]
    command += ['-I' + x for x in include]
    command += ['--fast-fail'] if fast_fail else []
    command += ['--cplus'] if cpp else []
    command += ['--embed'] if embed else []
    command += additional_flags

    return builder.build([command], None, outputs, foreach=True,
      metadata={'cython_outdir': outdir})

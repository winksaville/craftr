# Craftr [![Build Status](https://travis-ci.org/craftr-build/craftr.svg?branch=master)](https://travis-ci.org/craftr-build/craftr) [![Gitter](https://badges.gitter.im/Join%20Chat.svg)](https://gitter.im/craftr-build/craftr?utm_source=badge&utm_medium=badge&utm_campaign=pr-badge)

Craftr is a pythonic meta build system for Ninja that is fast,
cross-platform, easy to use and allows for fine grain control of
the build process. In Craftr, build definitions are generated
using Python scripts.

__The simplest possible example__

```python
# craftr_module(hello_world)

load_module('compiler')
cxx = compiler.CxxCompiler()
cxx.detect()

objects = cxx.objects(
  sources = glob(join(project_dir, 'source/**/*.cpp')),
)

main = cxx.executable(
  filename = 'main',
  inputs = [objects],
  default = True,
)

run = load_module('rules').run(
  executable = main,
)
```

Now simply run `craftr build` and the `main` target will be built.
Check out the [Wiki][] for additional information and examples!

## Installation

Grab the latest release from this repository and install using Pip.
You might want to do so in a virtualenv. To always use the latest
version of Craftr, consider using an editable installation (`-e`)
and update by pulling the latest changes into the repository.

    git clone git@github.com:craftr-build/craftr.git && cd craftr
    virtualenv .env && source .env/bin/activate
    pip install -e .

__Requirements__

- [ninja](https://github.com/martine/ninja)
- Python 3.4 or greater
- [glob2](pypi.python.org/pypi/glob2)
- [colorama](pypi.python.org/pypi/colorama) (optional)

----------

Copyright (C) 2015 Niklas Rosenstein

  [Wiki]: https://github.com/craftr-build/craftr/wiki

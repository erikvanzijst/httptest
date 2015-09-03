#!/usr/bin/env python
"""Installs httptest"""

import os
import sys
from distutils.core import setup

COMMANDS = {}

try:
    from wheel.bdist_wheel import bdist_wheel
except ImportError:
    pass
else:
    COMMANDS['bdist_wheel'] = bdist_wheel

def long_description():
    """Get the long description from the README"""
    return open(os.path.join(sys.path[0], 'README.rst')).read()

setup(
    author='Brodie Rao',
    author_email='brodie@sf.io',
    classifiers=[
        'Development Status :: 3 - Alpha',
        'Environment :: Web Environment',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: MIT License',
        'Natural Language :: English',
        'Operating System :: OS Independent',
        'Programming Language :: Python',
        'Programming Language :: Python :: 3',
        'Topic :: Internet :: WWW/HTTP :: HTTP Servers',
        'Topic :: Internet :: WWW/HTTP :: WSGI :: Server',
        'Topic :: Software Development :: Testing',
    ],
    cmdclass=COMMANDS,
    description='A real live HTTP server to use in tests',
    keywords='functional http test',
    license='MIT',
    long_description=long_description(),
    name='httptest',
    py_modules=['httptest'],
    version='0.5',
)

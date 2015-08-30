"""Tests for httptest"""

import doctest

import httptest

def load_tests(loader, tests, ignore):
    """Add httptest's doctests to the list of unit tests"""
    tests.addTests(doctest.DocTestSuite(httptest))
    return tests

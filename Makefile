PYTHON=python

ifdef PREFIX
PREFIX_ARG=--prefix=$(PREFIX)
endif

all: build

build:
	$(PYTHON) setup.py build

clean:
	-$(PYTHON) setup.py clean --all
	find . -not -path '*/.git/*' \( -name '*.py[cdo]' -o -name '*,cover' \
		-o -name __pycache__ \) -prune -exec rm -rf '{}' ';'
	rm -rf dist build htmlcov
	rm -f README.md MANIFEST .coverage

distclean: clean
	find . -not -path '*/.git/*' \( -name '*.orig' -o -name '*.rej' \) \
		-exec rm -f '{}' ';'
	rm -rf .tox

install: build
	$(PYTHON) setup.py install $(PREFIX_ARG)

dist:
	TAR_OPTIONS="--owner=root --group=root --mode=u+w,go-w,a+rX-s" \
	$(PYTHON) setup.py -q sdist

test:
	tox

tests: test

# E129: indentation between lines in conditions
# E261: two spaces before inline comment
# E301: expected blank line
# E302: two new lines between functions/etc.
pep8:
	pep8 --ignore=E129,E261,E301,E302 --repeat httptest.py setup.py

pyflakes:
	pyflakes httptest.py setup.py

pylint:
	pylint --rcfile=.pylintrc httptest.py setup.py

.PHONY: all build clean install dist test tests pep8 pyflakes pylint

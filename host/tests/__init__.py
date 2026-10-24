"""Host tests, kept in a package so their module names cannot collide.

``tests/`` holds the engine's suite and is collected without a package, so a
file named ``test_contracts.py`` there and a file of the same name here are two
different modules with the same import name. Running ``pytest tests host/tests``
in one command then fails with "import file mismatch".

Making this a package gives every module here a qualified name
(``host.tests.test_contracts``) and lets both suites run in one invocation,
which is what a pre-commit hook and a CI job both want. The alternative -
renaming the files - would hide the fact that they are the same *kind* of test.
"""

from setuptools import setup, find_packages
import sys, os

version = '0.1'

setup(
    name = 'aqs_sweeper',
    version = version,
    description = "Extendable Azure Queue Storage dumper that copy your data into Azure Blob Storage.",
    packages = find_packages( exclude = [ 'ez_setup'] ),
    include_package_data = True,
    zip_safe = False,
    entry_points={
        'console_scripts': [
            'aqs-sweep = aqs_sweeper:main',
        ],
    },
    author = 'Bence Faludi',
    author_email = 'bence@ozmo.hu',
    license = 'GPL',
    install_requires = [
        'azure-storage',
        'progressbar2',
    ],
    test_suite = "asqsweeper.tests"
)

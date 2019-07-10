from __future__ import print_function
import logging
import sys
# pylint: disable=wrong-import-position
import openaihub.func as func

logger = logging.getLogger(__name__)
logging.basicConfig(stream=sys.stdout, level=logging.INFO)

import click
from click import UsageError

@click.group()
@click.version_option()
def cli():
    pass

@cli.command()
@click.option("--path", metavar="NAME", required=True,
              help="")
@click.option("--operator", metavar="NAME", required=True,
              help="")
@click.option("--logpath", metavar="NAME", default='',
              help="")
@click.option("--version", "-v", metavar="VERSION",
              help="")
def register(path, operator, logpath, version):
    if logpath == '':
        logpath = path
    func.register(path, operator, logpath, version)
    
@cli.command()
@click.option("--namespace", "-e", metavar="NAME", default="default",
              help="")
@click.option("--version", "-v", metavar="VERSION",
              help="")
def install(namespace, version):
    func.install(namespace, version)

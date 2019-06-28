from __future__ import print_function
import logging
import sys
import subprocess
import time
from git import Repo
import tempfile
import os

logger = logging.getLogger(__name__)
logging.basicConfig(stream=sys.stdout, level=logging.INFO)

def run(cmd):
    ret = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    logger.info("command = %s, returncode = %s" % (ret.args, ret.returncode))
    return(ret)

import click
from click import UsageError

@click.group()
@click.version_option()
def cli():
    pass

@cli.command()
@click.option("--namespace", "-e", metavar="NAME", default="default",
              help="")
@click.option("--version", "-v", metavar="VERSION",
              help="")
def install(namespace, version):
    # clone the py-oah repo
    openaihub_git_url = "https://github.com/adrian555/py-oah.git"
    tempdir = tempfile.mkdtemp()
    basedir = os.path.join(tempdir, os.path.basename(openaihub_git_url))
    Repo.clone_from(openaihub_git_url, basedir)

    # prereq: helm must be installed already
    # init helm tiller service account
    logger.info("1/10 Init helm tiller...")
    run("kubectl create -f %s/src/requirement/helm-tiller.yaml" % basedir)
    run("helm init --service-account tiller --upgrade")

    openaihub_namespace = "operators"
    openaihub_catalog_path = "%s/src/registry/catalog_source" % basedir
    openaihub_subscription_path = "%s/src/registry/subscription" % basedir
    openaihub_cr_path = "%s/src/registry/cr_samples" % basedir

    # install OLM
    logger.info("2/10 Install OLM...")
    olm_version = "0.10.0"
    import wget
    wget.download("https://github.com/operator-framework/operator-lifecycle-manager/releases/download/%s/install.sh" % olm_version, out="%s/install.sh" % basedir)
    run("bash %s/install.sh %s" % (basedir, olm_version))

    # add openaihub catalog
    logger.info("3/10 Add OpenAIHub operators catalog...")
    run("kubectl apply -f %s/openaihub.catalogsource.yaml" % openaihub_catalog_path)

    # create kubeflow namespace
    run("kubectl create namespace kubeflow")

    # create jupyterlab operator
    logger.info("4/10 Deploy Jupyterlab operator...")
    run("kubectl apply -f %s/%s-operator.yaml" % (openaihub_subscription_path, "jupyterlab"))

    # create jupyterlab cr
    logger.info("5/10 Create Jupyterlab deployment...")
    run("kubectl apply -f %s/openaihub_v1alpha1_%s_cr.yaml -n %s" % (openaihub_cr_path, "jupyterlab", openaihub_namespace))

    # switch default storageclass to nfs-dynamic
    # TBD: add an exit condition
    logger.info("6/10 Wait for nfs-dynamic storageclass to be ready and set as default...")
    while (run("kubectl get storageclass |grep nfs-dynamic").stdout.decode() == ''):
        time.sleep(10)
    run("kubectl patch storageclass ibmc-file-bronze -p '{\"metadata\": {\"annotations\":{\"storageclass.kubernetes.io/is-default-class\":\"false\"}}}'")
    run("kubectl patch storageclass nfs-dynamic -p '{\"metadata\": {\"annotations\":{\"storageclass.kubernetes.io/is-default-class\":\"true\"}}}'")

    # create pipelines operator
    logger.info("7/10 Deploy Pipelines operator...")
    run("kubectl apply -f %s/%s-operator.yaml" % (openaihub_subscription_path, "pipelines"))

    # create pipelines cr
    logger.info("8/10 Create Pipelines deployment...")
    run("kubectl apply -f %s/openaihub_v1alpha1_%s_cr.yaml -n %s" % (openaihub_cr_path, "pipelines", openaihub_namespace))

    # create openaihub operator
    logger.info("9/10 Deploy OpenAIHub operator...")
    run("kubectl apply -f %s/%s-operator.yaml" % (openaihub_subscription_path, "openaihub"))

    # create openaihub cr
    logger.info("10/10 Create OpenAIHub deployment...")
    run("kubectl apply -f %s/openaihub_v1alpha1_%s_cr.yaml -n %s" % (openaihub_cr_path, "openaihub", openaihub_namespace))

    logger.info("Done.")

from __future__ import print_function
import logging
import sys
import subprocess
import time
from git import Repo
import tempfile
import os
import tarfile

logger = logging.getLogger(__name__)
logging.basicConfig(stream=sys.stdout, level=logging.INFO)

# clone the py-oah repo
openaihub_git_url = "https://github.com/adrian555/py-oah.git"
tempdir = tempfile.mkdtemp()
basedir = os.path.join(tempdir, os.path.basename(openaihub_git_url))
Repo.clone_from(openaihub_git_url, basedir)

def run(cmd):
    ret = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    logger.info("command: %s, returncode: %s" % (ret.args, ret.returncode))
    return(ret)

def wait_for(operator, namespace):
    while run("kubectl rollout status deployment/%s-operator -n %s" % (operator, namespace)).returncode != 0 :
        time.sleep(30)

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
@click.option("--version", "-v", metavar="VERSION",
              help="")
def register(path, operator, version):
    # unpack operator tgz files to src/registry/operators
    kaniko_path = os.path.join(basedir, "src/registry/kaniko")
    operator_path = os.path.join(kaniko_path, "operators", operator)
    os.mkdir(operator_path)
    tf = tarfile.open(os.path.join(path, operator + ".tgz"), "r:gz")
    tf.extractall(operator_path)

    # create context.tgz
    kaniko_tgz = os.path.join(basedir, "kaniko.tgz")
    tf = tarfile.open(kaniko_tgz, "w:gz")
    tf.add(kaniko_path, arcname="")
    tf.close()

    # create docker-config ConfigMap
    run("kubectl create ConfigMap docker-config --from-file=%s/config.json" % kaniko_path)

    # modify kaniko.yaml with operator destination
    run("sed -i 's/destination=.*$/destination=abc:v0.0.1\"/' %s/kaniko.yaml" % kaniko_path)

    # create kaniko pod
    run("kubectl apply -f %s/kaniko.yaml" % kaniko_path)

    # copy build context to kaniko container
    run("kubectl cp %s kaniko:/tmp/context.tar.gz -c kaniko-init" % kaniko_tgz)
    run("kubectl exec kaniko -c kaniko-init -- tar -zxf /tmp/context.tar.gz -C /kaniko/build-context")
    run("kubectl exec kaniko -c kaniko-init -- touch /tmp/complete")

    # now wait for the image to be built and ready
    
@cli.command()
@click.option("--namespace", "-e", metavar="NAME", default="default",
              help="")
@click.option("--version", "-v", metavar="VERSION",
              help="")
def install(namespace, version):
    # prereq: helm must be installed already
    # init helm tiller service account
    logger.info("### 1/13 ### Init helm tiller...")
    run("kubectl create -f %s/src/requirement/helm-tiller.yaml" % basedir)
    run("helm init --service-account tiller --upgrade")

    openaihub_namespace = "operators"
    openaihub_catalog_path = "%s/src/registry/catalog_source" % basedir
    openaihub_subscription_path = "%s/src/registry/subscription" % basedir
    openaihub_cr_path = "%s/src/registry/cr_samples" % basedir

    # install OLM
    logger.info("### 2/13 ### Install OLM...")
    olm_version = "0.10.0"
    import wget
    wget.download("https://github.com/operator-framework/operator-lifecycle-manager/releases/download/%s/install.sh" % olm_version, out="%s/install.sh" % basedir)
    run("bash %s/install.sh %s" % (basedir, olm_version))

    # add openaihub catalog
    logger.info("### 3/13 ### Add OpenAIHub operators catalog...")
    run("kubectl apply -f %s/openaihub.catalogsource.yaml" % openaihub_catalog_path)

    # create kubeflow namespace
    run("kubectl create namespace kubeflow")

    # create jupyterlab operator
    logger.info("### 4/13 ### Deploy Jupyterlab operator...")
    run("kubectl apply -f %s/%s-operator.yaml" % (openaihub_subscription_path, "jupyterlab"))

    # wait until jupyterlab operator is available
    logger.info("### 5/13 ### Wait until Jupyterlab operator is available...")
    wait_for("jupyterlab", openaihub_namespace)

    # create jupyterlab cr
    logger.info("### 6/13 ### Create Jupyterlab deployment...")
    run("kubectl apply -f %s/openaihub_v1alpha1_%s_cr.yaml -n %s" % (openaihub_cr_path, "jupyterlab", openaihub_namespace))

    # switch default storageclass to nfs-dynamic
    # TBD: add a timeout exit condition
    logger.info("### 7/13 ### Wait for nfs-dynamic storageclass to be ready and set as default...")
    while (run("kubectl get storageclass |grep nfs-dynamic").stdout.decode() == ''):
        time.sleep(30)
    run("kubectl patch storageclass ibmc-file-bronze -p '{\"metadata\": {\"annotations\":{\"storageclass.kubernetes.io/is-default-class\":\"false\"}}}'")
    run("kubectl patch storageclass nfs-dynamic -p '{\"metadata\": {\"annotations\":{\"storageclass.kubernetes.io/is-default-class\":\"true\"}}}'")

    # create pipelines operator
    logger.info("### 8/13 ### Deploy Pipelines operator...")
    run("kubectl apply -f %s/%s-operator.yaml" % (openaihub_subscription_path, "pipelines"))

    # wait until pipelines operator is available
    logger.info("### 9/13 ### Wait until Pipelines operator is available...")
    wait_for("pipelines", openaihub_namespace)

    # create pipelines cr
    logger.info("### 10/13 ### Create Pipelines deployment...")
    run("kubectl apply -f %s/openaihub_v1alpha1_%s_cr.yaml -n %s" % (openaihub_cr_path, "pipelines", openaihub_namespace))

    # create openaihub operator
    logger.info("### 11/13 ### Deploy OpenAIHub operator...")
    run("kubectl apply -f %s/%s-operator.yaml" % (openaihub_subscription_path, "openaihub"))

    # wait until openaihub operator is available
    logger.info("### 12/13 ### Wait until OpenAIHub operator is available...")
    wait_for("openaihub", openaihub_namespace)

    # create openaihub cr
    logger.info("### 13/13 ### Create OpenAIHub deployment...")
    run("kubectl apply -f %s/openaihub_v1alpha1_%s_cr.yaml -n %s" % (openaihub_cr_path, "openaihub", openaihub_namespace))

    logger.info("Done.")

from __future__ import print_function
import logging
import sys
import platform
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
    logger.addHandler(logging.FileHandler(os.path.join(path, "openaihub.log")))

    # unpack operator tgz files to src/registry/operators
    logger.info("### 1/8 ### Unpack operator tgz...")
    kaniko_path = os.path.join(basedir, "src/registry/kaniko")
    operator_path = os.path.join(kaniko_path, "operators", operator)
    os.makedirs(operator_path, exist_ok=True)
    tf = tarfile.open(os.path.join(path, operator + ".tgz"), "r:gz")
    tf.extractall(operator_path)

    # create context.tgz
    logger.info("### 2/8 ### Create build-context...")
    kaniko_tgz = os.path.join(basedir, "kaniko.tgz")
    tf = tarfile.open(kaniko_tgz, "w:gz")
    tf.add(os.path.join(kaniko_path, "Dockerfile"), arcname="Dockerfile")
    tf.add(os.path.join(kaniko_path, "operators"), arcname="operators")
    tf.close()

    # create docker-config ConfigMap
    logger.info("### 3/8 ### Create docker config...")
    run("kubectl create configmap docker-config --from-file=%s/config.json" % kaniko_path)

    # modify kaniko.yaml with operator destination
    logger.info("### 4/8 ### Create kaniko pod...")
    run("sed -i %s 's/destination=.*$/destination=docker.io\/ffdlops\/%s-catalog:v0.0.1\"/' %s/kaniko.yaml" % ("''" if platform.system() == 'Darwin' else '', operator, kaniko_path))

    # create kaniko pod
    run("kubectl apply -f %s/kaniko.yaml" % kaniko_path)

    # wait for the pod to be ready
    time.sleep(60)

    # copy build context to kaniko container
    logger.info("### 5/8 ### Set up kaniko job...")
    run("kubectl cp %s kaniko:/tmp/context.tar.gz -c kaniko-init" % kaniko_tgz)
    run("kubectl exec kaniko -c kaniko-init -- tar -zxf /tmp/context.tar.gz -C /kaniko/build-context")
    run("kubectl exec kaniko -c kaniko-init -- touch /tmp/complete")

    # now wait for the image to be built and ready
    logger.info("### 6/8 ### Wait for the image to be ready...")
    while run("kubectl get pod/kaniko|grep kaniko|awk '{ print $3;exit}'").stdout.decode() != "Completed\n" :
        time.sleep(30)

    # delete the kaniko pod
    logger.info("### 7/8 ### Delete the kaniko pod...")
    run("kubectl delete -f %s/kaniko.yaml" % kaniko_path)

    # generate catalog source yaml
    logger.info("### 8/8 ### Deploy the catalog...")
    run("sed -i %s 's/REPLACE_OPERATOR/%s/' %s/catalogsource.yaml" % ("''" if platform.system() == 'Darwin' else '', operator, kaniko_path))
    run("sed -i %s 's/REPLACE_IMAGE/docker.io\/ffdlops\/%s-catalog:v0.0.1/' %s/catalogsource.yaml" % ("''" if platform.system() == 'Darwin' else '', operator, kaniko_path))

    # deploy the catalog
    run("kubectl apply -f %s/catalogsource.yaml" % kaniko_path)

    logger.info("Done.")
    
@cli.command()
@click.option("--namespace", "-e", metavar="NAME", default="default",
              help="")
@click.option("--version", "-v", metavar="VERSION",
              help="")
def install(namespace, version):
    steps = 14

    # prereq: helm must be installed already
    # init helm tiller service account
    logger.info("### 1/%s ### Init helm tiller..." % steps)
    run("kubectl create -f %s/src/requirement/helm-tiller.yaml" % basedir)
    run("helm init --service-account tiller --upgrade")

    openaihub_namespace = "operators"
    openaihub_catalog_path = "%s/src/registry/catalog_source" % basedir
    openaihub_subscription_path = "%s/src/registry/subscription" % basedir
    openaihub_cr_path = "%s/src/registry/cr_samples" % basedir

    # install OLM
    logger.info("### 2/%s ### Install OLM..." % steps)
    olm_version = "0.10.0"
    import wget
    wget.download("https://github.com/operator-framework/operator-lifecycle-manager/releases/download/%s/install.sh" % olm_version, out="%s/install.sh" % basedir)
    run("bash %s/install.sh %s" % (basedir, olm_version))

    # add openaihub catalog
    logger.info("### 3/%s ### Add OpenAIHub operators catalog..." % steps)
    run("kubectl apply -f %s/openaihub.catalogsource.yaml" % openaihub_catalog_path)

    # create kubeflow namespace
    run("kubectl create namespace kubeflow")

    # create jupyterlab operator
    logger.info("### 4/%s ### Deploy Jupyterlab operator..." % steps)
    run("kubectl apply -f %s/%s-operator.yaml" % (openaihub_subscription_path, "jupyterlab"))

    # wait until jupyterlab operator is available
    logger.info("### 5/%s ### Wait until Jupyterlab operator is available..." % steps)
    wait_for("jupyterlab", openaihub_namespace)

    # create jupyterlab cr
    logger.info("### 6/%s ### Create Jupyterlab deployment..." % steps)
    run("kubectl apply -f %s/openaihub_v1alpha1_%s_cr.yaml -n %s" % (openaihub_cr_path, "jupyterlab", openaihub_namespace))

    # switch default storageclass to nfs-dynamic
    # TBD: add a timeout exit condition
    logger.info("### 7/%s ### Wait for nfs-dynamic storageclass to be ready and set as default..." % steps)
    while (run("kubectl get storageclass |grep nfs-dynamic").stdout.decode() == ''):
        time.sleep(30)
    run("kubectl patch storageclass ibmc-file-bronze -p '{\"metadata\": {\"annotations\":{\"storageclass.kubernetes.io/is-default-class\":\"false\"}}}'")
    run("kubectl patch storageclass nfs-dynamic -p '{\"metadata\": {\"annotations\":{\"storageclass.kubernetes.io/is-default-class\":\"true\"}}}'")

    # create pipelines operator
    logger.info("### 8/%s ### Deploy Pipelines operator..." % steps)
    run("kubectl apply -f %s/%s-operator.yaml" % (openaihub_subscription_path, "pipelines"))

    # wait until pipelines operator is available
    logger.info("### 9/%s ### Wait until Pipelines operator is available..." % steps)
    wait_for("pipelines", openaihub_namespace)

    # create pipelines cr
    logger.info("### 10/%s ### Create Pipelines deployment..." % steps)
    run("kubectl apply -f %s/openaihub_v1alpha1_%s_cr.yaml -n %s" % (openaihub_cr_path, "pipelines", openaihub_namespace))

    # create openaihub operator
    logger.info("### 11/%s ### Deploy OpenAIHub operator..." % steps)
    run("kubectl apply -f %s/%s-operator.yaml" % (openaihub_subscription_path, "openaihub"))

    # wait until openaihub operator is available
    logger.info("### 12/%s ### Wait until OpenAIHub operator is available..." % steps)
    wait_for("openaihub", openaihub_namespace)

    # create openaihub cr
    logger.info("### 13/%s ### Create OpenAIHub deployment..." % steps)
    run("kubectl apply -f %s/openaihub_v1alpha1_%s_cr.yaml -n %s" % (openaihub_cr_path, "openaihub", openaihub_namespace))

    # add cluster-admin to default service account for registration and installation of other operators
    logger.info("### 14/%s ### Add cluster admin..." % steps)
    run("kubectl create clusterrolebinding add-on-cluster-admin --clusterrole=cluster-admin --serviceaccount=%s:default" % openaihub_namespace)

    logger.info("Done.")

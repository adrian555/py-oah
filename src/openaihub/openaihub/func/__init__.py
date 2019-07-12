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
import shutil

logger = logging.getLogger(__name__)
logging.basicConfig(stream=sys.stdout, level=logging.INFO)

def run(cmd):
    ret = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    logger.info("Command: %s, Returncode: %s" % (ret.args, ret.returncode))
    if ret.returncode != 0:
        logger.info("Error: %s" % ret.stderr.decode()) 
    return(ret)

def wait_for(operator, namespace):
    # exit after 600 seconds
    # pylint: disable=unused-variable
    for x in range(40):
        if run("kubectl rollout status deployment/%s-operator -n %s" % (operator, namespace)).returncode != 0:
            time.sleep(15)
        else:
            break
        
def register(path, operator, logpath, version):
    logger.addHandler(logging.FileHandler(os.path.join(path, "openaihub-%s.log" % operator.lower())))

    # clone the py-oah repo
    openaihub_git_url = "https://github.com/adrian555/py-oah.git"
    tempdir = tempfile.mkdtemp()
    basedir = os.path.join(tempdir, os.path.basename(openaihub_git_url))
    Repo.clone_from(openaihub_git_url, basedir)

    steps = 8

    # unpack operator tgz files to src/registry/operators
    logger.info("### 1/%s ### Unpack operator tgz..." % steps)
    kaniko_path = os.path.join(basedir, "src/registry/kaniko")
    operator_path = os.path.join(kaniko_path, "operators", operator)
    os.makedirs(operator_path, exist_ok=True)
    tf = tarfile.open(os.path.join(path, operator + ".tgz"), "r:gz")
    tf.extractall(operator_path)

    # create context.tgz
    logger.info("### 2/%s ### Create build-context..." % steps)
    kaniko_tgz = os.path.join(basedir, "kaniko.tgz")
    tf = tarfile.open(kaniko_tgz, "w:gz")
    tf.add(os.path.join(kaniko_path, "Dockerfile"), arcname="Dockerfile")
    tf.add(os.path.join(kaniko_path, "operators"), arcname="operators")
    tf.close()

    # create docker-config ConfigMap
    logger.info("### 3/%s ### Create docker config..." % steps)
    run("kubectl create configmap docker-config --from-file=%s/config.json" % kaniko_path)

    # modify kaniko.yaml with operator destination
    kaniko_pod = "kaniko-" + operator.lower()
    logger.info("### 4/%s ### Create kaniko pod..." % steps)
    run("sed -i %s 's/IMAGETAG/docker.io\/ffdlops\/%s-catalog:v0.0.1/' %s/kaniko.yaml" % ("''" if platform.system() == 'Darwin' else '', operator.lower(), kaniko_path))
    run("sed -i %s 's/OPERATOR/%s/' %s/kaniko.yaml" % ("''" if platform.system() == 'Darwin' else '', operator.lower(), kaniko_path))

    # create kaniko pod
    run("kubectl apply -f %s/kaniko.yaml" % kaniko_path)

    # wait for the pod to be ready
    time.sleep(60)

    # copy build context to kaniko container
    logger.info("### 5/%s ### Set up kaniko job..." % steps)
    run("kubectl cp %s %s:/tmp/context.tar.gz -c kaniko-init" % (kaniko_tgz, kaniko_pod))
    run("kubectl exec %s -c kaniko-init -- tar -zxf /tmp/context.tar.gz -C /kaniko/build-context" % kaniko_pod)
    run("kubectl exec %s -c kaniko-init -- touch /tmp/complete" % kaniko_pod)

    # now wait for the image to be built and ready
    logger.info("### 6/%s ### Wait for the image to be ready..." % steps)
    # pylint: disable=unused-variable
    for x in range(40):
        if run("kubectl get pod/%s|grep %s|awk '{ print $3;exit}'" % (kaniko_pod, kaniko_pod)).stdout.decode() != "Completed\n" :
            time.sleep(15)
        else:
            break

    # delete the kaniko pod
    logger.info("### 7/%s ### Delete the kaniko pod..." % steps)
    run("kubectl delete -f %s/kaniko.yaml" % kaniko_path)

    # generate catalog source yaml
    logger.info("### 8/%s ### Deploy the catalog..." % steps)
    run("sed -i %s 's/REPLACE_OPERATOR/%s/' %s/catalogsource.yaml" % ("''" if platform.system() == 'Darwin' else '', operator, kaniko_path))
    run("sed -i %s 's/REPLACE_IMAGE/docker.io\/ffdlops\/%s-catalog:v0.0.1/' %s/catalogsource.yaml" % ("''" if platform.system() == 'Darwin' else '', operator.lower(), kaniko_path))

    # deploy the catalog
    run("kubectl apply -f %s/catalogsource.yaml" % kaniko_path)

    # remove temp
    shutil.rmtree(basedir, ignore_errors=True)

    logger.info("Done.")

def install(namespace, version):
    # clone the py-oah repo
    openaihub_git_url = "https://github.com/adrian555/py-oah.git"
    tempdir = tempfile.mkdtemp()
    basedir = os.path.join(tempdir, os.path.basename(openaihub_git_url))
    Repo.clone_from(openaihub_git_url, basedir)
    
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
    # pylint: disable=unused-variable
    for x in range(40):
        if run("kubectl get storageclass |grep nfs-dynamic").stdout.decode() == '':
            time.sleep(15)
        else:
            break
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

    # remove temp
    shutil.rmtree(basedir, ignore_errors=True)

    logger.info("Done.")

__all__ = ["install", "register"]
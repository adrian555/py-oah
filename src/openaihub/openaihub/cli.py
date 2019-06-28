import subprocess
import time
from git import Repo
import tempfile

def run(cmd):
    return(subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE))

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
    run("kubectl create -f %s/src/requirement/helm-tiller.yaml" % basedir)
    run("helm init --service-account tiller --upgrade")

    openaihub_namespace = "operators"
    openaihub_catalog_path = "%s/src/registry/catalog_source" % basedir
    openaihub_subscription_path = "%s/src/registry/subscription" % basedir
    openaihub_cr_path = "%s/src/registry/cr_samples" % basedir

    # install OLM
    olm_version = "0.10.0"
    import wget
    wget.download("https://github.com/operator-framework/operator-lifecycle-manager/releases/download/%s/install.sh" % olm_version, out="/tmp/install.sh")
    run("bash /tmp/install.sh %s" % olm_version)

    # add openaihub catalog
    run("kubectl apply -f %s/openaihub.catalogsource.yaml" % openaihub_catalog_path)

    # create kubeflow namespace
    run("kubectl create namespace kubeflow")

    # create jupyterlab operator
    run("kubectl apply -f %s/%s-operator.yaml" % (openaihub_subscription_path, "jupyterlab"))

    # create jupyterlab cr
    run("kubectl apply -f %s/openaihub_v1alpha1_%s_cr.yaml -n %s" % (openaihub_cr_path, "jupyterlab", openaihub_namespace))

    # switch default storageclass to nfs-dynamic
    # TBD: add an exit condition
    while (run("kubectl get storageclass |grep nfs-dynamic").stdout.decode() == ''):
        time.sleep(10)
    run("kubectl patch storageclass ibmc-file-bronze -p '{\"metadata\": {\"annotations\":{\"storageclass.kubernetes.io/is-default-class\":\"false\"}}}'")
    run("kubectl patch storageclass nfs-dynamic -p '{\"metadata\": {\"annotations\":{\"storageclass.kubernetes.io/is-default-class\":\"true\"}}}'")

    # create pipelines operator
    run("kubectl apply -f %s/%s-operator.yaml" % (openaihub_subscription_path, "pipelines"))

    # create pipelines cr
    run("kubectl apply -f %s/openaihub_v1alpha1_%s_cr.yaml -n %s" % (openaihub_cr_path, "pipelines", openaihub_namespace))

    # create openaihub operator
    run("kubectl apply -f %s/%s-operator.yaml" % (openaihub_subscription_path, "openaihub"))

    # create openaihub cr
    run("kubectl apply -f %s/openaihub_v1alpha1_%s_cr.yaml -n %s" % (openaihub_cr_path, "openaihub", openaihub_namespace))

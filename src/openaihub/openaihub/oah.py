import subprocess
import time

def run(cmd):
    return(subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE))

# prereq: helm must be installed already
# init helm tiller service account
run("kubectl create -f /Users/wzhuang/py-oah/src/requirement/helm-tiller.yaml")
run("helm init --service-account tiller --upgrade")

openaihub_namespace = "operators"
openaihub_subscription_path = "/Users/wzhuang/py-oah/src/registry/subscription"
openaihub_cr_path = "/Users/wzhuang/py-oah/src/registry/cr_samples"

# install OLM
olm_version = "0.10.0"
import wget
wget.download("https://github.com/operator-framework/operator-lifecycle-manager/releases/download/%s/install.sh" % olm_version, out="/tmp/install.sh")
run("bash /tmp/install.sh %s" % olm_version)

# add openaihub catalog
openaihub_catalog_path = "/Users/wzhuang/py-oah/src/registry/catalog_source"
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

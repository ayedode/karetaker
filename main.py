import typer
from typing import Optional, List
from rich import print
import os
import sys
from loguru import logger
from rich.console import Console
from rich.table import Table
import time

console = Console()


app = typer.Typer()

state = {
    "verbose": False,
    "mode": None,
    "kubernetes": {
        "client": None
    },
    "docker": {
        "client": None
    }
  }

def get_mode():
  mode_env = os.getenv('KARETAKER_MODE')
  if mode_env != "":
    return mode_env

  if os.path.exists("/.dockerenv"):
    return "docker"
  
  if os.path.exists("/var/run/secrets/kubernetes.io"):
    return "kubernetes"
   
def get_interval():
  interval_env = os.getenv("KARETAKER_INTERVAL")

  if interval_env:
      return interval_env
  else:
    return 3600

def get_kubeconfig_path():
    home = os.path.expanduser('~')
    kubeconfig_env = os.getenv('KUBECONFIG')

    kubeconfig_path = os.path.join(home, ".kube", "config")

    if kubeconfig_env != "":
        kubeconfig_path = kubeconfig_env
    
    return kubeconfig_path

def kubernetes_prune(namespace):
  client = state["kubernetes"]["client"]

  '''
    node=$(kubectl get node | grep "worker" | grep "NotReady" | awk '{print $1}')
    for pod in $(kubectl get pods -A -o=JSON --field-selector spec.nodeName=${node} | jq -r '.items[] |
    select(.metadata.namespace | startswith("bi-")) | .metadata.name') ; do
      namespace=$(kubectl get pods -A -o=JSON --field-selector metadata.name=${pod}| jq -r ' .items[] .metadata.namespace')
      echo "Killing pod ${namespace}/${pod}"
      kubectl delete pod -n ${namespace} --force ${pod}
    done
  '''

  pods = []
  nodes = []
  try:
    # Prepare nodes
    node_list = client.list_node()
    for node in node_list.items:
      _node = {
          "name": node.metadata.name,
          "scheduling": node.spec.unschedulable,
          "status": "Not ready"
      }

      node_scheduling = node.spec.unschedulable

      for condition in node.status.conditions:
          if condition.type == "Ready" and condition.status:
              _node["status"] = "Ready"
              break
      if node_scheduling is None or not node_scheduling:
          pass
      else:
        _node["status"] = f"{_node['status']},SchedulingDisabled"
      nodes.append(_node)

    # Prepare pods
    if namespace:
      for n in namespace:
        pod_list = client.list_namespaced_pod(n)

        if len(pod_list.items) < 1:
          logger.warning(f"No pods in namespace '{n}' or namespace doesn't exist")
        else:
          for pod in pod_list.items:
            pods.append(pod)

    else:
      pod_list = client.list_pod_for_all_namespaces(watch=False)
      for pod in pod_list.items:
          pods.append(pod)

  except Exception as e:
      logger.error(e)
      raise typer.Abort()
  
  table = Table("Pod", "Namespace", "Node", "Status", "Dead?")
  for pod in pods:
    dead = False
    status = pod.status.phase

    '''
    if pod.DeletionTimestamp != nil && pod.Status.Reason == node.NodeUnreachablePodReason { 
      reason = "Unknown" 
    } else if pod.DeletionTimestamp != nil { 
      reason = "Terminating" 
    } 
    '''
    if hasattr(pod, "deletion_timestamp"):
        # Pod is marked for deletion
        # Check if it's on a dead node
        for node in nodes:
          if pod.spec.node_name == node["name"] and node["status"] != "Ready":
            status = "Terminating"
            dead = True
    
    table.add_row(pod.metadata.name, pod.metadata.namespace, pod.spec.node_name, status, str(dead))
  console.print(table)

def docker_prune(dangling: bool = False):
  client = state["docker"]["client"]

  pruned = {
      "images": {
        "count": 0,
        "size": 0,
      },
      "containers": {
        "count": 0,
        "size": 0,
      },
      "networks": {
        "count": 0,
      },
      "volumes": {
        "count": 0,
        "size": 0,
      },
      "count": 0,
      "size": 0,
  }

  # Images
  pruned_images = client.images.prune(filters={"dangling": dangling})
  
  if pruned_images['ImagesDeleted']:
    pruned["images"]["count"] = len(pruned_images['ImagesDeleted'])
    pruned["count"] += len(pruned_images['ImagesDeleted'])

  pruned["images"]["size"] = pruned_images['SpaceReclaimed']
  pruned["size"] += pruned_images['SpaceReclaimed']

  # Containers
  pruned_containers = client.containers.prune()
  
  if pruned_containers['ContainersDeleted']:
    pruned["containers"]["count"] = len(pruned_containers['ContainersDeleted'])
    pruned["count"] += len(pruned_containers['ContainersDeleted'])

  pruned["containers"]["size"] = pruned_containers['SpaceReclaimed']
  pruned["size"] += pruned_containers['SpaceReclaimed']
  
  # Volumes
  pruned_volumes = client.volumes.prune()
  
  if pruned_volumes['VolumesDeleted']:
    pruned["volumes"]["count"] = len(pruned_volumes['VolumesDeleted'])
    pruned["count"] += len(pruned_volumes['VolumesDeleted'])

  pruned["volumes"]["size"] = pruned_volumes['SpaceReclaimed']
  pruned["size"] += pruned_volumes['SpaceReclaimed']
  
  # Networks
  pruned_networks = client.networks.prune()
  
  if pruned_networks['NetworksDeleted']:
    pruned["networks"]["count"] = len(pruned_networks['NetworksDeleted'])
    pruned["count"] += len(pruned_networks['NetworksDeleted'])

  # Final output
  logger.info(f"pruned {pruned['images']['count']} images ({pruned['images']['size']/1024/1024/1024} GB), {pruned['containers']['count']} containers ({pruned['containers']['size']/1024/1024/1024} GB), {pruned['volumes']['count']} volumes ({pruned['volumes']['size']/1024/1024/1024} GB), {pruned['networks']['count']} networks. Total: {pruned['count']} objects ({pruned['size']/1024/1024/1024} GB)")

@logger.catch
@app.command()
def run(dangling: bool = False):
    while True:
      match state["mode"]:
        case "docker":
          docker_prune(dangling)
        case "kubernetes":
          kubernetes_prune(namespace)
        case _:
          logger.error("No mode chosen")
          raise typer.Abort()
      
      logger.info(f"sleeping for {state['interval']}s")
      time.sleep(state["interval"])

@logger.catch
@app.callback()
def main(
    verbose: bool = False, 
    mode: str = get_mode(), 
    kubeconfig: str = get_kubeconfig_path(), 
    incluster: bool = True,
    interval: int = get_interval(),
   ):
    """
    Karetaker. Your friendly garbage collector for Docker and Kubernetes.
    """

    loglevel = "INFO"
    if verbose:
        loglevel = "DEBUG"
        state["verbose"] = True

    config = {
        "handlers": [
            {"sink": sys.stdout, "level": loglevel, "format": "{time} | {level} | {message}"},
        ],
    }
    logger.configure(**config)

    # Detect mode
    if not mode:
       logger.error("failed to auto-detect mode. Please set '--mode docker' or '--mode kubernetes'")
       raise typer.Abort()
    state["mode"] = mode

    logger.info(f"running in mode: {mode}")

    state["interval"] = interval
    

    logger.debug(f"Engine: {mode}")

    if mode == "kubernetes":
      from kubernetes import client, config, watch

      if incluster:
        config.load_incluster_config()
      else:
        logger.info(f"Loading config file from {kubeconfig}")
        config.load_kube_config(config_file=kubeconfig)

      state["kubernetes"]["client"] = client.CoreV1Api()
      state["kubernetes"]["watcher"] = watch.Watch()
    elif mode == "docker":
      import docker
      try:
        client = docker.from_env()
      except Exception as e:
        logger.error(f"unable to connect to the docker engine: {e}")
        logger.error(f"if karetaker is running inside a docker container, don't forget to mount the docker socket")
        raise typer.Abort()

      state["docker"]["client"] = client
       
    else:
        logger.error("currently no engine apart from 'docker' and 'kubernetes' is supported")
        raise typer.Abort()


if __name__ == "__main__":
    app()

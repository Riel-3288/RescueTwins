"""Generate reviewable host-placement manifests; apply only to organizer-provided nodes."""
import argparse
import json
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--image", required=True, help="Built and pushed RescueTwin image, preferably an immutable digest")
parser.add_argument("--config", default=str(ROOT / "config.production.example.json"))
parser.add_argument("--output", default=str(ROOT / "k8s/template.yaml"))
args = parser.parse_args()
cfg_text = Path(args.config).read_text()
config = json.loads(cfg_text)
if not config.get("require_tls"):
    raise ValueError("shared deployment requires TLS")
namespace = "rescuetwin"
docs = [{"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": namespace}},
        {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "integration-config", "namespace": namespace}, "data": {"config.json": cfg_text}},
        {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "mqtt-config", "namespace": namespace}, "data": {"mosquitto.conf": (ROOT/"broker/mosquitto-tls.conf").read_text(), "acl": (ROOT/"broker/mosquitto.acl").read_text()}}]


def pvc(name):
    docs.append({"apiVersion": "v1", "kind": "PersistentVolumeClaim", "metadata": {"name": name, "namespace": namespace},
                 "spec": {"accessModes": ["ReadWriteOnce"], "resources": {"requests": {"storage": "1Gi"}}}})


def workload(name, image, command, port, zone, env, cert_secret, extra_volumes=(), extra_mounts=()):
    pvc(name+"-data")
    container = {"name": name, "image": image, "env": env,
                 "volumeMounts": [{"name": "data", "mountPath": "/mosquitto/data" if name == "mqtt" else "/data"},
                                  {"name": "certs", "mountPath": "/mosquitto/certs" if name == "mqtt" else "/certs", "readOnly": True}, *extra_mounts]}
    if command:
        container["command"] = command
    if port:
        container["ports"] = [{"containerPort": port}]
        container["readinessProbe"] = {"tcpSocket": {"port": port}, "initialDelaySeconds": 5, "periodSeconds": 5}
        docs.append({"apiVersion": "v1", "kind": "Service", "metadata": {"name": name, "namespace": namespace},
                     "spec": {"selector": {"app": name}, "ports": [{"port": port, "targetPort": port}]}})
    spec = {"containers": [container], "securityContext": {"fsGroup": 1883},
            "volumes": [{"name": "data", "persistentVolumeClaim": {"claimName": name+"-data"}},
                        {"name": "certs", "secret": {"secretName": cert_secret, "defaultMode": 288}}, *extra_volumes]}
    if zone:
        spec["nodeSelector"] = {"rescuetwin-mec-zone": zone}
    docs.append({"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": name, "namespace": namespace},
                 "spec": {"replicas": 1, "strategy": {"type": "Recreate"}, "selector": {"matchLabels": {"app": name}},
                          "template": {"metadata": {"labels": {"app": name}}, "spec": spec}}})


def secret_env(name, key):
    return {"name": name, "valueFrom": {"secretKeyRef": {"name": "cse-tokens", "key": key}}}


for suffix, zone, kind, port, cse_id, cse_name in [("a", "A", "MN", 18001, "id-mn-a", "cse-mn-a"), ("b", "B", "MN", 18002, "id-mn-b", "cse-mn-b"), ("in", None, "IN", 18000, "id-in", "cse-in")]:
    name = "cse-" + suffix
    env = [{"name": key, "value": str(value)} for key, value in {"CSE_TYPE": kind, "CSE_ID": cse_id, "CSE_NAME": cse_name, "CSE_PORT": port, "CSE_HOST": name, "CSE_TLS": "true"}.items()]
    env.append(secret_env("CSE_SERVER_TOKEN", suffix))
    workload(name, args.image, ["python", "tools/acme_config.py"], port, zone, env, name+"-certs")
for name, action in [("bridge", "bridge"), ("mec-ipe", "ipe")]:
    env = [secret_env("CSE_A_TOKEN", "a"), secret_env("CSE_B_TOKEN", "b"), secret_env("CSE_IN_TOKEN", "in")]
    if action == "ipe":
        env.append({"name": "MEC_TOKEN", "valueFrom": {"secretKeyRef": {"name": "mec-access", "key": "token", "optional": True}}})
    workload(name, args.image, ["python", "-m", "rescuetwin", "--config", "/config/config.json", action], None, "A", env, "bridge-certs",
             extra_volumes=[{"name": "config", "configMap": {"name": "integration-config"}}], extra_mounts=[{"name": "config", "mountPath": "/config", "readOnly": True}])
workload("mqtt", "eclipse-mosquitto:2.0.22", None, 8883, "A", [], "mqtt-certs",
         extra_volumes=[{"name": "config", "configMap": {"name": "mqtt-config"}}], extra_mounts=[{"name": "config", "mountPath": "/mosquitto/config", "readOnly": True}])
Path(args.output).write_text(yaml.safe_dump_all(docs, sort_keys=False), encoding="utf-8")
print("Generated", args.output)

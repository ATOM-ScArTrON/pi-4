"""Pi-side mTLS provisioning client and keyset validator."""

import json
import os
import ssl
import stat
from urllib.request import Request, urlopen
from wearable.ui.terminal import display_on_terminal

print = display_on_terminal


def validate_keyset(payload):
    if not isinstance(payload.get("device_id"), str) or not payload["device_id"].strip():
        raise ValueError("provisioning response must contain device_id")
    epoch = payload.get("key_epoch", payload.get("mission_epoch_id"))
    if not isinstance(epoch, int) or epoch < 0:
        raise ValueError("provisioning response must contain a non-negative key_epoch")
    keyset = payload.get("mission_keyset")
    if not isinstance(keyset, dict):
        raise ValueError("mission_keyset must be a {peer_id: key} mapping")
    for peer_id, key_hex in keyset.items():
        if not isinstance(peer_id, str) or not isinstance(key_hex, str) or len(bytes.fromhex(key_hex)) != 16:
            raise ValueError("every keyset entry must be peer_id -> 16-byte hex key")
    broadcast = payload.get("mission_broadcast_key", "")
    if len(bytes.fromhex(broadcast)) != 16:
        raise ValueError("mission_broadcast_key must be 16 bytes")
    return payload


def provision(server_url, device_id, output_path, ca_file, cert_file, key_file):
    context = ssl.create_default_context(cafile=ca_file)
    context.load_cert_chain(cert_file, key_file)
    request = Request(server_url.rstrip("/") + "/device/register",
                      data=json.dumps({"device_id": device_id}).encode("utf-8"),
                      headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(request, context=context, timeout=15) as response:
        payload = validate_keyset(json.loads(response.read().decode("utf-8")))
        if payload["device_id"] != device_id:
            raise ValueError("provisioning response device_id does not match this device")
    directory = os.path.dirname(output_path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    temporary = output_path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2)
    os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR)
    os.replace(temporary, output_path)
    return payload


def run_standalone():
    payload = provision(os.environ["PROVISION_SERVER_URL"], os.environ["DEVICE_ID"],
                        os.environ["MISSION_KEYSET_PATH"], os.environ["TLS_CA_FILE"],
                        os.environ["TLS_CERT_FILE"], os.environ["TLS_KEY_FILE"])
    print(f"Provisioned {len(payload['mission_keyset'])} pairwise peer keys.")


if __name__ == "__main__":
    provision(os.environ["PROVISION_SERVER_URL"], os.environ["DEVICE_ID"],
              os.environ["MISSION_KEYSET_PATH"], os.environ["TLS_CA_FILE"],
              os.environ["TLS_CERT_FILE"], os.environ["TLS_KEY_FILE"])

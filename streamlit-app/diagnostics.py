from kubernetes.client.rest import ApiException

def diagnose_pod(v1, pod_name, namespace):

    # Fetch Pod
    try:
        pod = v1.read_namespaced_pod(name=pod_name, namespace=namespace)
    except ApiException as e:
        return {
            "summary": f"Pod '{pod_name}' could not be found in the cluster.",
            "likely_cause": "The pod name may be incorrect or it no longer exists in this namespace.",
            "evidence": str(e),
            "recommendation": "Double-check the pod name and selected namespace, then try again."
        }


    # Events
    try:
        events = v1.list_namespaced_event(
            namespace,
            field_selector=f"involvedObject.name={pod_name}"
        ).items
    except Exception:
        events = []

    event_messages = [f"{e.reason}: {e.message}" for e in events]


    # Logs
    try:
        logs = v1.read_namespaced_pod_log(
            name=pod_name,
            namespace=namespace,
            tail_lines=50
        )
    except Exception:
        logs = ""

    log_text = logs.lower() if isinstance(logs, str) else ""


    # Status Info
    phase = pod.status.phase
    reason = ""
    last_termination_reason = ""
    container_statuses = pod.status.container_statuses or []

    for c in container_statuses:
        if c.state.waiting:
            reason = c.state.waiting.reason or ""
        elif c.state.terminated:
            reason = c.state.terminated.reason or ""

        if c.last_state and c.last_state.terminated:
            last_termination_reason = c.last_state.terminated.reason or ""


    restart_count = sum([c.restart_count for c in container_statuses])


    # IMAGE PULL FAILURE
    if "imagepullbackoff" in reason.lower() or "errimagepull" in reason.lower():
        return {
            "summary": "The pod is unable to download the container image.",
            "likely_cause": "The specified image name or tag may not exist, or access to the registry is denied.",
            "evidence": event_messages,
            "recommendation": (
                "Verify the image name and tag in your Deployment file. "
                "Also ensure the image exists and that Kubernetes has permission to pull it."
            )
        }


    # SCHEDULING / VOLUME ISSUES
    for e in event_messages:
        if "FailedScheduling" in e or "FailedMount" in e:
            return {
                "summary": "The pod could not be scheduled or mounted correctly.",
                "likely_cause": "There are insufficient node resources or an issue with volume/PVC attachment.",
                "evidence": event_messages,
                "recommendation": (
                    "Check node CPU & memory availability, verify PersistentVolumeClaims, "
                    "and ensure volume mounts are correctly configured."
                )
            }


    # DNS FAILURE
    if any(k in log_text for k in [
        "no such host", "temporary failure in name resolution", "nxdomain", "servfail"
    ]):
        return {
            "summary": "The pod is unable to resolve DNS hostnames.",
            "likely_cause": "Cluster DNS (CoreDNS) issue or incorrect service hostname being used.",
            "evidence": logs.splitlines()[-15:],
            "recommendation": (
                "Ensure CoreDNS pods are running and verify that the hostname or service name is correct. "
                "Try running nslookup inside the pod to confirm DNS resolution."
            )
        }


    # PERMISSION ERROR
    if "permission denied" in log_text:
        return {
            "summary": "The application failed due to file permission restrictions.",
            "likely_cause": "The container does not have sufficient permissions for required files or directories.",
            "evidence": logs.splitlines()[-15:],
            "recommendation": (
                "Adjust file permissions, container user settings, or volume access rights."
            )
        }


    # NETWORK TIMEOUT
    if "timeout" in log_text:
        return {
            "summary": "The pod experienced network connectivity delays.",
            "likely_cause": "The application could not reach another service or external endpoint.",
            "evidence": logs.splitlines()[-15:],
            "recommendation": (
                "Verify target service availability, network policies, and DNS configuration."
            )
        }


    # OOM KILL
    if "oomkilled" in last_termination_reason.lower():
        return {
            "summary": "The pod was terminated due to excessive memory usage.",
            "likely_cause": "The application exceeded its assigned memory limit.",
            "evidence": {
                "restart_count": restart_count,
                "logs": logs.splitlines()[-10:]
            },
            "recommendation": (
                "Increase memory limits or optimize the application's memory consumption."
            )
        }


    # PROBE FAILURE
    probe_failures = [e for e in event_messages if "probe failed" in e.lower()]
    if probe_failures:
        return {
            "summary": "Health checks are failing for this pod.",
            "likely_cause": "The pod is not responding properly to readiness or liveness probes.",
            "evidence": probe_failures,
            "recommendation": (
                "Ensure your health endpoints (/health or /ready) return HTTP 200 consistently."
            )
        }


    # IGNORE SCANNER TRAFFIC
    ignore_patterns = [
        "hnap1", "solr", "cgi-bin", "masscan", "nmap",
        "paloaltonetworks", "odin", "favicon.ico", "go-http-client"
    ]

    if any(p in log_text for p in ignore_patterns):
        return {
            "summary": "Non-critical external scan traffic detected.",
            "likely_cause": "Automated internet scanners probing the exposed service.",
            "evidence": logs.splitlines()[-10:],
            "recommendation": "This is normal behavior. No action is required."
        }


    # APPLICATION ERRORS
    if any(err in log_text for err in ["error", "exception", "fatal", "panic"]):
        return {
            "summary": "Application-level errors detected in logs.",
            "likely_cause": "Internal code or configuration issue in the application.",
            "evidence": logs.splitlines()[-15:],
            "recommendation": (
                "Review detailed logs and fix application-level issues."
            )
        }


    # CRASH LOOP BACKOFF (LAST PRIORITY)
    if "crashloopbackoff" in reason.lower():
        backoff_events = [e for e in event_messages if "back-off" in e.lower() or "crash" in e.lower()]

        return {
            "summary": f"Pod '{pod_name}' is repeatedly crashing and restarting.",
            "likely_cause": "The container process is failing during startup, causing Kubernetes to restart it continuously.",
            "evidence": {
                "restart_count": restart_count,
                "state": "CrashLoopBackOff",
                "relevant_events": backoff_events[:5],
                "last_logs": logs.splitlines()[-10:]
            },
            "recommendation": (
                "Inspect application startup behavior and verify configuration, "
                "environment variables, and required dependencies."
            ),
        }


    # PENDING POD
    if phase == "Pending":
        return {
            "summary": "The pod is waiting to be scheduled.",
            "likely_cause": "Insufficient node resources or scheduling conflicts.",
            "evidence": event_messages,
            "recommendation": "Check node capacity and scaling configuration."
        }


    # HEALTHY POD
    if phase == "Running":
        return {
            "summary": "The pod is healthy and running normally.",
            "likely_cause": "No operational issues detected.",
            "evidence": logs.splitlines()[-10:],
            "recommendation": "No intervention required."
        }


    # UNKNOWN
    return {
        "summary": f"No clear failure pattern found for pod '{pod_name}'.",
        "likely_cause": "The issue does not match any known diagnostic patterns.",
        "evidence": {
            "phase": phase,
            "restart_count": restart_count,
            "events": event_messages,
            "logs": logs.splitlines()[-10:]
        },
        "recommendation": "Inspect logs and describe output manually for deeper analysis."
    }

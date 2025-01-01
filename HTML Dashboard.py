#!/usr/bin/env python3
"""
Build By Valentin Vina
===============================================================================
Enhanced Isilon Python Script (Multi-Cluster Option, with timeouts & debug prints)
 - Interactive menu
 - Modern HTML dashboard
 - NFS & SMB JSON parsed into table
 - Cluster time & NTP listing
 - Quota usage gracefully handled
 - ADDED: AuditRate function in CLI & Dashboard
 - ADDED: Automatic upload of auditrates.sh to /root/auditrates.sh
 - NEW: Option to monitor multiple clusters at once, generating a single
        combined HTML with separate sections for each cluster's data
 - ADDED: Timeout in invoke_ssh_command, and debug prints in multi-cluster loop

Edits:
  * `safe_cluster_id(...)` used for HTML IDs.
  * `invoke_ssh_command(...)` reads line-by-line for 'auditrates.sh' or 'isi_audit_viewer'
    to stop once "Total average:" is seen, preventing indefinite waits.
===============================================================================
"""

import os
import re
import csv
import getpass
import paramiko
import smtplib
import json
from datetime import datetime
from typing import List, Dict, Optional
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
import socket  # ADDED

###############################################################################
# GLOBAL SETTINGS
###############################################################################
SSH_CLIENT = None  # (retained for single-cluster mode)
SFTEMPFILE = None
TODAY = datetime.now()

REPORT_FOLDER = r"C:\Users\X\Desktop\MY SCRIPTS\isiwrapper-master\isiwrapper-master"
timestamp_str = TODAY.strftime("%Y%m%d_%H%M%S")
HTML_REPORT = os.path.join(REPORT_FOLDER, f"{timestamp_str} - IsilonDashboard.html")

MAIL_SERVER = "mailhost"
MAIL_PORT = 25
MAIL_TO = "valentin.vina@XYZ.net"
MAIL_CC = ""
MAIL_FROM = "valentin.vina@XYZ.net"
MAIL_SUBJECT = f"Isilon Dashboard on {TODAY.strftime('%A')} {TODAY.strftime('%B %d, %Y')} at {TODAY.strftime('%H:%M:%S')}"

SSH_COMMAND_TIMEOUT = 300  # 5 minutes, adjust as you wish

###############################################################################
# TEMP FILE INIT
###############################################################################
def _init_tempfile():
    global SFTEMPFILE
    temp_dir = os.path.join(os.path.expanduser("~"), ".tmp")
    if not os.path.exists(temp_dir):
        os.makedirs(temp_dir)
    sft = datetime.now().strftime("sftempfile-%Y%m%d-%H%M%S.csv")
    SFTEMPFILE = os.path.join(temp_dir, sft)
    if os.path.exists(SFTEMPFILE):
        os.remove(SFTEMPFILE)

_init_tempfile()

###############################################################################
# ADDED: ALIAS to avoid NameError (dummy function referencing itself).
###############################################################################
def create_html_dashboard_alias(cluster_name: str):
    """
    Alias to ensure create_html_dashboard is recognized.
    Calls the real create_html_dashboard.
    """
    return create_html_dashboard(cluster_name)

###############################################################################
# SSH HELPER FUNCTIONS
###############################################################################
def connect_isilon_cluster(cluster_name: str, username: str, password: str) -> List[str]:
    """
    Connect-IsilonCluster equivalent (single cluster).
    """
    global SSH_CLIENT
    try:
        SSH_CLIENT = paramiko.SSHClient()
        SSH_CLIENT.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        SSH_CLIENT.connect(
            cluster_name,
            username=username,
            password=password,
            look_for_keys=False,
            allow_agent=False
        )
        return [f"Successfully connected to {cluster_name} via SSH."]
    except Exception as e:
        return [f"ERROR: {e}"]

def disconnect_isilon_cluster(cluster_name: str) -> List[str]:
    global SSH_CLIENT
    if SSH_CLIENT:
        SSH_CLIENT.close()
        SSH_CLIENT = None
        return [f"Disconnected from {cluster_name}"]
    return ["No active SSH session to disconnect."]

def invoke_ssh_command(command: str) -> List[str]:
    """
    Run a command with a specified timeout (SSH_COMMAND_TIMEOUT) to avoid indefinite hangs.

    If the command includes 'auditrates.sh' or 'isi_audit_viewer', we do a line-by-line read
    until we see "Total average:" or the channel closes, preventing indefinite block
    once the script has effectively ended.
    """
    global SSH_CLIENT
    if not SSH_CLIENT:
        raise ValueError("SSH session not established. Connect first.")

    # By default
    local_timeout = SSH_COMMAND_TIMEOUT

    # Potentially long
    if "auditrates.sh" in command or "isi_audit_viewer" in command:
        print("[DEBUG] Detected a potentially long-running command. Increasing local timeout to 3600 seconds.")
        local_timeout = 3600

    # Execute once
    stdin, stdout, stderr = SSH_CLIENT.exec_command(command, timeout=local_timeout)
    # If command is normal, just read the standard out
    if not ("auditrates.sh" in command or "isi_audit_viewer" in command):
        out = stdout.read().decode(errors="replace").splitlines()
        err = stderr.read().decode(errors="replace").splitlines()
        return out + err

    # Otherwise, for 'auditrates.sh' or 'isi_audit_viewer':
    # We'll read line-by-line to detect "Total average:".
    out_lines = []
    err_lines = []
    channel = stdout.channel
    done_keywords = ["Total average:"]  # If we see that, we consider the script finished.

    channel.settimeout(5.0)
    # Keep reading lines until exit_status_ready or we see done_keywords
    found_done = False
    try:
        while True:
            if channel.exit_status_ready():
                # remote side has closed
                break
            try:
                line = stdout.readline()
            except socket.timeout:
                # If no data for 5s
                if channel.exit_status_ready():
                    break
                continue  # else keep reading
            if not line:
                # Possibly no more data right now
                if channel.exit_status_ready():
                    break
                continue
            # We got a line
            line_strip = line.rstrip("\r\n")
            out_lines.append(line_strip)
            # If "Total average:" is found, we consider it ended
            for kw in done_keywords:
                if kw in line_strip:
                    found_done = True
                    break
            if found_done:
                break
    except Exception as ex:
        # We'll store a warning but not remove other lines
        out_lines.append(f"[WARN] {str(ex)}")

    # Also read any remainder from stderr
    err_data = stderr.read().decode(errors="replace")
    if err_data:
        err_lines.extend(err_data.splitlines())

    return out_lines + err_lines

###############################################################################
# (NEW) Upload the Audit Rate script to /root/auditrates.sh
###############################################################################
def upload_audit_rate_script() -> None:
    global SSH_CLIENT

    script_content = """#!/bin/bash

# Obtain the cluster name, trim whitespace, convert to lowercase
CLUSTER_NAME=$(isi cluster identity view | grep 'Name:' | cut -d: -f2 | awk '{gsub(/[[:space:]]+/, ""); print tolower($0)}')

# Generate DATE1 and DATE2 for the last 24 hours
DATE1=$(date -v-24H "+%Y-%m-%d %H:%M:%S") # 24 hours ago
DATE2=$(date "+%Y-%m-%d %H:%M:%S") # Now

# Append the current timestamp and cluster name to the filename in auditrates_clustername_YYYYMMDDHHMM format
CURRENT_TIMESTAMP=$(date "+%Y%m%d%H%M")
SAVE_FILE="./auditrates_${CLUSTER_NAME}_${CURRENT_TIMESTAMP}.txt"

################ Do not edit any values below ################

EPOCH1=$(date -j -f "%Y-%m-%d %H:%M:%S" "$DATE1" "+%s")
EPOCH2=$(date -j -f "%Y-%m-%d %H:%M:%S" "$DATE2" "+%s")
DIFF=$(expr $EPOCH2 - $EPOCH1)

isi status -q

declare -a event_array

for node in $(isi_for_array hostname | cut -d: -f1 | sed 's/^.*-\\([0-9]*\\)$/\\1/' | sort -n)
do
  echo -e "node $node:\\t"
  echo -e "Results $(date) \\nnode $node:\\t" >> "$SAVE_FILE"
  EVENTS=$(isi_audit_viewer -n $node -t protocol -s "$DATE1" -e "$DATE2" | wc -l)
  echo "Seconds: $DIFF"
  echo "Events: $EVENTS"
  event_array+=($EVENTS)
  echo "Average rate: $(perl -E "say $EVENTS / $DIFF") evts/s"
  echo "Average rate: $(perl -E "say $EVENTS / $DIFF") evts/s" >> "$SAVE_FILE"
done

TOTAL=0

for EVENT in ${event_array[@]}; do
  TOTAL=$(expr $TOTAL + $EVENT)
done

echo "Total average: $(perl -E "say $TOTAL / $DIFF") evts/s"
echo "Total average: $(perl -E "say $TOTAL / $DIFF") evts/s" >> "$SAVE_FILE"
"""

    if not SSH_CLIENT:
        print("No SSH connection to upload script. Connect first.")
        return

    sftp = SSH_CLIENT.open_sftp()
    try:
        remote_path = "/root/auditrates.sh"
        with sftp.open(remote_path, 'w') as f:
            f.write(script_content)
        sftp.chmod(remote_path, 0o755)
    finally:
        sftp.close()

###############################################################################
# BASIC GET-ISILON-X
###############################################################################
def get_isilon_status(cluster_name: str) -> List[str]:
    cmd = "isi status"
    return invoke_ssh_command(cmd)

def get_isilon_battery_status(cluster_name: str) -> List[str]:
    cmd = "isi batterystatus list"
    return invoke_ssh_command(cmd)

def get_isilon_read_write_status(cluster_name: str) -> List[str]:
    cmd = "isi readonly list"
    return invoke_ssh_command(cmd)

def get_isilon_disk_usage(cluster_name: str) -> List[str]:
    cmd = "isi_for_array -s df -ik | grep -v 1024-blocks"
    return invoke_ssh_command(cmd)

def get_isilon_nics(cluster_name: str) -> List[str]:
    cmd = "isi network interfaces list"
    return invoke_ssh_command(cmd)

def get_isilon_version(cluster_name: str) -> List[str]:
    cmd = "isi version"
    return invoke_ssh_command(cmd)

###############################################################################
# (NEW) GET CLUSTER TIME + NTP
###############################################################################
def get_isilon_time_and_ntp(cluster_name: str) -> Dict[str, List[str]]:
    print(f"[DEBUG] Gathering cluster time for {cluster_name} ...")
    time_cmd = "isi_for_array -s date"
    time_out = invoke_ssh_command(time_cmd)

    print(f"[DEBUG] Gathering NTP servers for {cluster_name} ...")
    ntp_cmd = "isi ntp servers list"
    ntp_out = invoke_ssh_command(ntp_cmd)

    return {
        "cluster_time": time_out,
        "ntp_info": ntp_out
    }

###############################################################################
# (1) TIME SYNC with Domain
###############################################################################
def set_isilon_sync_time_with_domain(cluster_name: str, domain: str) -> List[str]:
    cmd = f"isi_for_array -s isi_classic auth ads time --sync --domain={domain} --force"
    return invoke_ssh_command(cmd)

###############################################################################
# (2) QUOTA USAGE REPORT
###############################################################################
def get_quota_usage_report(cluster_name: str) -> List[str]:
    print(f"[DEBUG] Gathering Quota Usage for {cluster_name} ...")
    cmd = "isi quota quotas list --format json"
    return invoke_ssh_command(cmd)

###############################################################################
# (3) NFS REPORT
###############################################################################
def get_isilon_nfs_report(cluster_name: str) -> List[str]:
    print(f"[DEBUG] Gathering NFS Exports for {cluster_name} ...")
    cmd = "isi nfs exports list --format json"
    return invoke_ssh_command(cmd)

###############################################################################
# (4) SMB REPORT
###############################################################################
def get_isilon_smb_report(cluster_name: str) -> List[str]:
    print(f"[DEBUG] Gathering SMB Shares for {cluster_name} ...")
    cmd = "isi smb share list --format json"
    return invoke_ssh_command(cmd)

###############################################################################
# (NEW) AUDIT RATE
###############################################################################
def run_isilon_audit_rate(cluster_name: str) -> List[str]:
    print(f"[DEBUG] Running AuditRate script for {cluster_name} ...")
    script_cmd = "bash /root/auditrates.sh"
    return invoke_ssh_command(script_cmd)

###############################################################################
# MENU
###############################################################################
def print_menu():
    print("\n=== ISILON MENU ===")
    print("1)  Get Battery Status           (isi batterystatus list)")
    print("2)  Get Isilon Status            (isi status)")
    print("3)  Get Isilon Version           (isi version)")
    print("4)  Get Disk Usage               (df -ik)")
    print("5)  Get NIC Info                 (isi network interfaces list)")
    print("6)  Generate HTML Dashboard      (with all info)")
    print("7)  Set Time with Domain         (Set-IsilonSyncTimeWithDomain)")
    print("8)  Quota Usage Report           (isi quota quotas list)")
    print("9)  NFS Report                   (isi nfs exports list)")
    print("10) SMB Report                  (isi smb share list)")
    print("11) Audit Rate                   (Runs AuditRate script)")
    print("D)  Disconnect from cluster")
    print("X/Q) Quit (same as exit)")
    print("==============================================")

def menu_loop(cluster_name: str):
    while True:
        print_menu()
        choice = input("Choose an option: ").strip().lower()
        if choice == "1":
            out = get_isilon_battery_status(cluster_name)
            print("\n-- Battery Status --")
            for line in out:
                print(line)

        elif choice == "2":
            out = get_isilon_status(cluster_name)
            print("\n-- Isilon Status --")
            for line in out:
                print(line)

        elif choice == "3":
            out = get_isilon_version(cluster_name)
            print("\n-- Isilon Version --")
            for line in out:
                print(line)

        elif choice == "4":
            out = get_isilon_disk_usage(cluster_name)
            print("\n-- Disk Usage --")
            for line in out:
                print(line)

        elif choice == "5":
            out = get_isilon_nics(cluster_name)
            print("\n-- NIC Info --")
            for line in out:
                print(line)

        elif choice == "6":
            create_html_dashboard(cluster_name)

        elif choice == "7":
            domain = input("Enter domain name (e.g. ADDOMAIN): ").strip()
            out = set_isilon_sync_time_with_domain(cluster_name, domain)
            print("\n-- Set Time With Domain Output --")
            for line in out:
                print(line)

        elif choice == "8":
            out = get_quota_usage_report(cluster_name)
            print("\n-- Quota Usage Report --")
            if not out:
                print("No quotas found.")
            else:
                for line in out:
                    print(line)

        elif choice == "9":
            out = get_isilon_nfs_report(cluster_name)
            print("\n-- NFS Configuration Report --")
            if not out:
                print("No NFS exports found.")
            else:
                for line in out:
                    print(line)

        elif choice == "10":
            out = get_isilon_smb_report(cluster_name)
            print("\n-- SMB Configuration Report --")
            if not out:
                print("No SMB shares found.")
            else:
                for line in out:
                    print(line)

        elif choice == "11":
            print("\n-- Attempting to upload script and run Audit Rate --")
            upload_audit_rate_script()
            out = run_isilon_audit_rate(cluster_name)
            for line in out:
                print(line)

        elif choice == "d":
            disc = disconnect_isilon_cluster(cluster_name)
            print("\n".join(disc))

        elif choice in ["x", "q"]:
            print("Exiting script. If not disconnected, SSH may remain open.")
            break

        else:
            print("Invalid choice, please try again.")

###############################################################################
# HTML HELPERS: Generating Table from JSON
###############################################################################
def generate_nfs_html_table(json_str: str) -> str:
    try:
        exports = json.loads(json_str)
    except Exception:
        return f"<p>ERROR parsing NFS JSON.</p><pre>{json_str}</pre>"

    if not exports:
        return "<p>No NFS Exports Found.</p>"

    lines = []
    lines.append("<table class='table table-bordered table-sm'><thead><tr>")
    lines.append("<th>ID</th><th>Description</th><th>Paths</th><th>Read_Only?</th><th>ReadWrite Clients</th><th>Root Clients</th>")
    lines.append("</tr></thead><tbody>")

    for exp in exports:
        exp_id = exp.get("id", "")
        desc   = exp.get("description", "")
        paths  = exp.get("paths", [])
        ro     = str(exp.get("read_only", False))
        rwc    = exp.get("read_write_clients", [])
        rc     = exp.get("root_clients", [])

        paths_str = "<br>".join(paths) if paths else ""
        rwc_str   = "<br>".join(rwc) if rwc else ""
        rc_str    = "<br>".join(rc) if rc else ""

        lines.append("<tr>")
        lines.append(f"<td>{exp_id}</td>")
        lines.append(f"<td>{desc}</td>")
        lines.append(f"<td>{paths_str}</td>")
        lines.append(f"<td>{ro}</td>")
        lines.append(f"<td>{rwc_str}</td>")
        lines.append(f"<td>{rc_str}</td>")
        lines.append("</tr>")

    lines.append("</tbody></table>")
    return "\n".join(lines)

def generate_smb_html_table(json_str: str) -> str:
    try:
        shares = json.loads(json_str)
    except Exception:
        return f"<p>ERROR parsing SMB JSON.</p><pre>{json_str}</pre>"

    if not shares:
        return "<p>No SMB Shares Found.</p>"

    lines = []
    lines.append("<table class='table table-bordered table-sm'><thead><tr>")
    lines.append("<th>ID</th><th>Name</th><th>Path</th><th>Description</th><th>Browsable?</th><th>Permissions</th>")
    lines.append("</tr></thead><tbody>")

    for share in shares:
        sid   = share.get("id", "")
        name  = share.get("name", "")
        path  = share.get("path", "")
        desc  = share.get("description", "")
        brow  = str(share.get("browsable", False))

        perms = share.get("permissions", [])
        perms_str = []
        for p in perms:
            pmode = p.get("permission", "")
            ptyp  = p.get("permission_type", "")
            trustee = p.get("trustee", {}).get("id","")
            perms_str.append(f"{pmode}({ptyp}) => {trustee}")
        perms_final = "<br>".join(perms_str) if perms_str else "None"

        lines.append("<tr>")
        lines.append(f"<td>{sid}</td>")
        lines.append(f"<td>{name}</td>")
        lines.append(f"<td>{path}</td>")
        lines.append(f"<td>{desc}</td>")
        lines.append(f"<td>{brow}</td>")
        lines.append(f"<td>{perms_final}</td>")
        lines.append("</tr>")

    lines.append("</tbody></table>")
    return "\n".join(lines)

def generate_quota_html_table(json_str: str) -> str:
    try:
        quotas = json.loads(json_str)
    except Exception:
        return f"<p>ERROR parsing Quota JSON.</p><pre>{json_str}</pre>"

    if not quotas:
        return "<p>No quotas found.</p>"

    lines = []
    lines.append("<table class='table table-bordered table-sm'><thead><tr>")
    lines.append("<th>Type</th><th>Path</th><th>Hard Threshold</th><th>Usage Derived</th>")
    lines.append("</tr></thead><tbody>")

    for q in quotas:
        q_type = q.get("type", "")
        path   = q.get("path", "")
        thresh = ""
        if "thresholds" in q:
            hard_val = q["thresholds"].get("hard", None)
            thresh   = str(hard_val) if hard_val else ""
        usage  = q.get("usage_derived", 0)

        lines.append("<tr>")
        lines.append(f"<td>{q_type}</td>")
        lines.append(f"<td>{path}</td>")
        lines.append(f"<td>{thresh}</td>")
        lines.append(f"<td>{usage}</td>")
        lines.append("</tr>")

    lines.append("</tbody></table>")
    return "\n".join(lines)

BOOTSTRAP_CSS = """
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.2.3/dist/css/bootstrap.min.css" rel="stylesheet">
"""

BOOTSTRAP_JS = """
<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.2.3/dist/js/bootstrap.bundle.min.js"></script>
"""

def safe_cluster_id(cluster_name: str) -> str:
    """
    Replaces invalid ID chars (like dots) with underscores for accordion IDs.
    Example: '10.154.0.71' -> '10_154_0_71'
    """
    return re.sub(r'[^A-Za-z0-9_-]+', '_', cluster_name)

def create_html_dashboard(cluster_name: str):
    if not SSH_CLIENT:
        print("No SSH session. Please connect first.")
        return

    upload_audit_rate_script()

    print(f"[DEBUG] Gathering cluster time/NTP for cluster: {cluster_name}")
    time_data = get_isilon_time_and_ntp(cluster_name)
    cluster_time_lines = time_data["cluster_time"]
    ntp_lines = time_data["ntp_info"]

    print(f"[DEBUG] Gathering status for cluster: {cluster_name}")
    status_data   = get_isilon_status(cluster_name)
    print(f"[DEBUG] Gathering battery for cluster: {cluster_name}")
    battery_data  = get_isilon_battery_status(cluster_name)
    print(f"[DEBUG] Gathering read/write for cluster: {cluster_name}")
    rw_data       = get_isilon_read_write_status(cluster_name)
    print(f"[DEBUG] Gathering disk usage for cluster: {cluster_name}")
    disk_data     = get_isilon_disk_usage(cluster_name)
    print(f"[DEBUG] Gathering NIC info for cluster: {cluster_name}")
    nic_data      = get_isilon_nics(cluster_name)

    print(f"[DEBUG] Gathering quota usage for cluster: {cluster_name}")
    quota_out = get_quota_usage_report(cluster_name)
    quota_json = "\n".join(quota_out) if quota_out else "[]"
    quota_table_html = generate_quota_html_table(quota_json)

    print(f"[DEBUG] Gathering NFS for cluster: {cluster_name}")
    nfs_out = get_isilon_nfs_report(cluster_name)
    nfs_json_str = "\n".join(nfs_out) if nfs_out else "[]"
    nfs_table_html = generate_nfs_html_table(nfs_json_str)

    print(f"[DEBUG] Gathering SMB for cluster: {cluster_name}")
    smb_out = get_isilon_smb_report(cluster_name)
    smb_json_str = "\n".join(smb_out) if smb_out else "[]"
    smb_table_html = generate_smb_html_table(smb_json_str)

    print(f"[DEBUG] Gathering audit rate for cluster: {cluster_name}")
    audit_out = run_isilon_audit_rate(cluster_name)
    if not audit_out:
        audit_panel = "<pre>No audit rate output.</pre>"
    else:
        audit_panel = "<pre>" + "\n".join(audit_out) + "</pre>"

    html = build_single_cluster_html(
        cluster_name, cluster_time_lines, ntp_lines,
        status_data, battery_data, rw_data, disk_data, nic_data,
        quota_table_html, nfs_table_html, smb_table_html, audit_panel
    )

    os.makedirs(os.path.dirname(HTML_REPORT), exist_ok=True)
    with open(HTML_REPORT, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Modern HTML Dashboard saved to: {HTML_REPORT}")

    choice = input("Send this HTML via email? (y/n): ").strip().lower()
    if choice.startswith("y"):
        send_html_via_email(HTML_REPORT)
        print("Email sent (if no exceptions).")

def build_single_cluster_html(
    cluster_name: str,
    cluster_time_lines: List[str],
    ntp_lines: List[str],
    status_data: List[str],
    battery_data: List[str],
    rw_data: List[str],
    disk_data: List[str],
    nic_data: List[str],
    quota_table_html: str,
    nfs_table_html: str,
    smb_table_html: str,
    audit_panel: str
) -> str:
    cluster_id = safe_cluster_id(cluster_name)

    html_lines = []
    html_lines.append("<html>")
    html_lines.append("<head>")
    html_lines.append(f"<title>Isilon Dashboard ({cluster_name})</title>")
    html_lines.append(BOOTSTRAP_CSS)
    html_lines.append("</head>")
    html_lines.append("<body class='bg-light'>")

    html_lines.append(f"""
<nav class="navbar navbar-expand-lg navbar-dark bg-primary">
  <div class="container-fluid">
    <a class="navbar-brand" href="#">Isilon Dashboard - {cluster_name}</a>
  </div>
</nav>
""")

    html_lines.append("<div class='container mt-4'>")
    html_lines.append(f"<h1 class='mb-3'>Daily Isilon Overview for {cluster_name}</h1>")

    # Use cluster_id in all IDs
    html_lines.append("<div class='accordion' id='accordionExample'>")

    # Time & NTP
    html_lines.append(f"""
<div class="accordion-item">
  <h2 class="accordion-header" id="headingTime_{cluster_id}">
    <button class="accordion-button" type="button" data-bs-toggle="collapse" data-bs-target="#collapseTime_{cluster_id}" aria-expanded="true" aria-controls="collapseTime_{cluster_id}">
      Cluster Time & NTP
    </button>
  </h2>
  <div id="collapseTime_{cluster_id}" class="accordion-collapse collapse show" aria-labelledby="headingTime_{cluster_id}" data-bs-parent="#accordionExample">
    <div class="accordion-body">
      <h5>Cluster Time (isi_for_array -s date)</h5>
      <pre>""")
    if cluster_time_lines:
        for ln in cluster_time_lines:
            html_lines.append(ln)
    else:
        html_lines.append("No cluster time data.")
    html_lines.append("</pre>")

    html_lines.append("<h5>NTP Servers (isi ntp servers list)</h5><pre>")
    if ntp_lines:
        for ln in ntp_lines:
            html_lines.append(ln)
    else:
        html_lines.append("No NTP data.")
    html_lines.append("</pre></div></div></div>")

    # Status
    html_lines.append(f"""
<div class="accordion-item">
  <h2 class="accordion-header" id="headingStatus_{cluster_id}">
    <button class="accordion-button collapsed" type="button" data-bs-toggle="collapse" data-bs-target="#collapseStatus_{cluster_id}" aria-expanded="false" aria-controls="collapseStatus_{cluster_id}">
      Cluster Status
    </button>
  </h2>
  <div id="collapseStatus_{cluster_id}" class="accordion-collapse collapse" aria-labelledby="headingStatus_{cluster_id}" data-bs-parent="#accordionExample">
    <div class="accordion-body">
      <pre>""")
    for ln in status_data:
        html_lines.append(ln)
    html_lines.append("</pre></div></div></div>")

    # Battery
    html_lines.append(f"""
<div class="accordion-item">
  <h2 class="accordion-header" id="headingBattery_{cluster_id}">
    <button class="accordion-button collapsed" type="button" data-bs-toggle="collapse" data-bs-target="#collapseBattery_{cluster_id}" aria-expanded="false" aria-controls="collapseBattery_{cluster_id}">
      Battery Status
    </button>
  </h2>
  <div id="collapseBattery_{cluster_id}" class="accordion-collapse collapse" aria-labelledby="headingBattery_{cluster_id}" data-bs-parent="#accordionExample">
    <div class="accordion-body">
      <pre>""")
    for ln in battery_data:
        html_lines.append(ln)
    html_lines.append("</pre></div></div></div>")

    # RW
    html_lines.append(f"""
<div class="accordion-item">
  <h2 class="accordion-header" id="headingRW_{cluster_id}">
    <button class="accordion-button collapsed" type="button" data-bs-toggle="collapse" data-bs-target="#collapseRW_{cluster_id}" aria-expanded="false" aria-controls="collapseRW_{cluster_id}">
      Read/Write Status
    </button>
  </h2>
  <div id="collapseRW_{cluster_id}" class="accordion-collapse collapse" aria-labelledby="headingRW_{cluster_id}" data-bs-parent="#accordionExample">
    <div class="accordion-body">
      <pre>""")
    for ln in rw_data:
        html_lines.append(ln)
    html_lines.append("</pre></div></div></div>")

    # Disk usage
    html_lines.append(f"""
<div class="accordion-item">
  <h2 class="accordion-header" id="headingDisk_{cluster_id}">
    <button class="accordion-button collapsed" type="button" data-bs-toggle="collapse" data-bs-target="#collapseDisk_{cluster_id}" aria-expanded="false" aria-controls="collapseDisk_{cluster_id}">
      Disk Usage
    </button>
  </h2>
  <div id="collapseDisk_{cluster_id}" class="accordion-collapse collapse" aria-labelledby="headingDisk_{cluster_id}" data-bs-parent="#accordionExample">
    <div class="accordion-body">
      <pre>""")
    for ln in disk_data:
        html_lines.append(ln)
    html_lines.append("</pre></div></div></div>")

    # NIC usage
    html_lines.append(f"""
<div class="accordion-item">
  <h2 class="accordion-header" id="headingNIC_{cluster_id}">
    <button class="accordion-button collapsed" type="button" data-bs-toggle="collapse" data-bs-target="#collapseNIC_{cluster_id}" aria-expanded="false" aria-controls="collapseNIC_{cluster_id}">
      NIC Info
    </button>
  </h2>
  <div id="collapseNIC_{cluster_id}" class="accordion-collapse collapse" aria-labelledby="headingNIC_{cluster_id}" data-bs-parent="#accordionExample">
    <div class="accordion-body">
      <pre>""")
    for ln in nic_data:
        html_lines.append(ln)
    html_lines.append("</pre></div></div></div>")

    # Quota
    html_lines.append(f"""
<div class="accordion-item">
  <h2 class="accordion-header" id="headingQuota_{cluster_id}">
    <button class="accordion-button collapsed" type="button" data-bs-toggle="collapse" data-bs-target="#collapseQuota_{cluster_id}" aria-expanded="false" aria-controls="collapseQuota_{cluster_id}">
      Quota Usage Report
    </button>
  </h2>
  <div id="collapseQuota_{cluster_id}" class="accordion-collapse collapse" aria-labelledby="headingQuota_{cluster_id}" data-bs-parent="#accordionExample">
    <div class="accordion-body">
    """)
    html_lines.append(quota_table_html)
    html_lines.append("</div></div></div>")

    # NFS
    html_lines.append(f"""
<div class="accordion-item">
  <h2 class="accordion-header" id="headingNFS_{cluster_id}">
    <button class="accordion-button collapsed" type="button" data-bs-toggle="collapse" data-bs-target="#collapseNFS_{cluster_id}" aria-expanded="false" aria-controls="collapseNFS_{cluster_id}">
      NFS Configuration Report
    </button>
  </h2>
  <div id="collapseNFS_{cluster_id}" class="accordion-collapse collapse" aria-labelledby="headingNFS_{cluster_id}" data-bs-parent="#accordionExample">
    <div class="accordion-body">
    """)
    html_lines.append(nfs_table_html)
    html_lines.append("</div></div></div>")

    # SMB
    html_lines.append(f"""
<div class="accordion-item">
  <h2 class="accordion-header" id="headingSMB_{cluster_id}">
    <button class="accordion-button collapsed" type="button" data-bs-toggle="collapse" data-bs-target="#collapseSMB_{cluster_id}" aria-expanded="false" aria-controls="collapseSMB_{cluster_id}">
      SMB Configuration Report
    </button>
  </h2>
  <div id="collapseSMB_{cluster_id}" class="accordion-collapse collapse" aria-labelledby="headingSMB_{cluster_id}" data-bs-parent="#accordionExample">
    <div class="accordion-body">
    """)
    html_lines.append(smb_table_html)
    html_lines.append("</div></div></div>")

    # Audit
    html_lines.append(f"""
<div class="accordion-item">
  <h2 class="accordion-header" id="headingAudit_{cluster_id}">
    <button class="accordion-button collapsed" type="button" data-bs-toggle="collapse" data-bs-target="#collapseAudit_{cluster_id}" aria-expanded="false" aria-controls="collapseAudit_{cluster_id}">
      Audit Rate
    </button>
  </h2>
  <div id="collapseAudit_{cluster_id}" class="accordion-collapse collapse" aria-labelledby="headingAudit_{cluster_id}" data-bs-parent="#accordionExample">
    <div class="accordion-body">
    """)
    html_lines.append(audit_panel)
    html_lines.append("</div></div></div>")

    html_lines.append("</div>")  # close .accordion
    html_lines.append("</div>")  # close .container
    html_lines.append(BOOTSTRAP_JS)
    html_lines.append("</body></html>")

    return "\n".join(html_lines)

def build_multi_cluster_html(cluster_results: Dict[str, Dict[str, str]]) -> str:
    lines = []
    lines.append("<html><head>")
    lines.append("<title>Multi-Cluster Isilon Dashboard</title>")
    lines.append(BOOTSTRAP_CSS)
    lines.append("</head><body class='bg-light'>")

    lines.append("""
<nav class="navbar navbar-expand-lg navbar-dark bg-primary">
  <div class="container-fluid">
    <a class="navbar-brand" href="#">Isilon Multi-Cluster Dashboard</a>
  </div>
</nav>
""")

    lines.append("<div class='container mt-4'>")
    lines.append("<h1>Multi-Cluster Combined Overview</h1>")

    lines.append("<div class='accordion' id='MultiClusterAccordion'>")

    idx = 0
    for c_name, c_data in cluster_results.items():
        idx += 1
        c_html = c_data['html']

        lines.append(f"""
<div class="accordion-item">
  <h2 class="accordion-header" id="headingMulti_{idx}">
    <button class="accordion-button collapsed" type="button" data-bs-toggle="collapse" data-bs-target="#collapseMulti_{idx}" aria-expanded="false" aria-controls="collapseMulti_{idx}">
      Cluster: {c_name}
    </button>
  </h2>
  <div id="collapseMulti_{idx}" class="accordion-collapse collapse" aria-labelledby="headingMulti_{idx}" data-bs-parent="#MultiClusterAccordion">
    <div class="accordion-body">
""")

        body_only = c_html
        body_only = re.sub(r"(?is)<html.*?>", "", body_only)
        body_only = re.sub(r"(?is)</html>", "", body_only)
        body_only = re.sub(r"(?is)<head.*?>.*?</head>", "", body_only)
        body_only = re.sub(r"(?is)<body.*?>", "", body_only)
        body_only = re.sub(r"(?is)</body>", "", body_only)

        lines.append(body_only)
        lines.append("</div></div></div>")

    lines.append("</div>")  # MultiClusterAccordion
    lines.append("</div>")  # container
    lines.append(BOOTSTRAP_JS)
    lines.append("</body></html>")
    return "\n".join(lines)

def handle_multiple_clusters_mode(num_clusters: int):
    cluster_results = {}

    for i in range(num_clusters):
        print(f"\n=== Cluster {i+1} of {num_clusters} ===")
        c_ip = input("Enter the Isilon cluster IP/Hostname: ").strip()
        c_user = input("Enter your username: ").strip()
        c_pass = getpass.getpass("Enter your password: ")

        print(f"[DEBUG] Connecting to cluster {c_ip} ...")
        connect_res = connect_isilon_cluster(c_ip, c_user, c_pass)
        for line in connect_res:
            print(line)
        if any("ERROR" in x for x in connect_res):
            print(f"Skipping {c_ip} due to connection error.")
            continue

        print(f"[DEBUG] Uploading script to cluster {c_ip} ...")
        upload_audit_rate_script()

        print(f"[DEBUG] Gathering data for cluster {c_ip} ...")
        time_data = get_isilon_time_and_ntp(c_ip)
        cluster_time_lines = time_data["cluster_time"]
        ntp_lines = time_data["ntp_info"]

        print(f"[DEBUG] get_isilon_status for {c_ip}")
        status_data   = get_isilon_status(c_ip)
        print(f"[DEBUG] get_isilon_battery_status for {c_ip}")
        battery_data  = get_isilon_battery_status(c_ip)
        print(f"[DEBUG] get_isilon_read_write_status for {c_ip}")
        rw_data       = get_isilon_read_write_status(c_ip)
        print(f"[DEBUG] get_isilon_disk_usage for {c_ip}")
        disk_data     = get_isilon_disk_usage(c_ip)
        print(f"[DEBUG] get_isilon_nics for {c_ip}")
        nic_data      = get_isilon_nics(c_ip)

        print(f"[DEBUG] get_quota_usage_report for {c_ip}")
        quota_out = get_quota_usage_report(c_ip)
        quota_json = "\n".join(quota_out) if quota_out else "[]"
        quota_table_html = generate_quota_html_table(quota_json)

        print(f"[DEBUG] get_isilon_nfs_report for {c_ip}")
        nfs_out = get_isilon_nfs_report(c_ip)
        nfs_json_str = "\n".join(nfs_out) if nfs_out else "[]"
        nfs_table_html = generate_nfs_html_table(nfs_json_str)

        print(f"[DEBUG] get_isilon_smb_report for {c_ip}")
        smb_out = get_isilon_smb_report(c_ip)
        smb_json_str = "\n".join(smb_out) if smb_out else "[]"
        smb_table_html = generate_smb_html_table(smb_json_str)

        print(f"[DEBUG] run_isilon_audit_rate for {c_ip}")
        out_audit = run_isilon_audit_rate(c_ip)
        if not out_audit:
            audit_panel = "<pre>No audit rate output.</pre>"
        else:
            audit_panel = "<pre>" + "\n".join(out_audit) + "</pre>"

        print(f"[DEBUG] Building single cluster HTML for {c_ip}")
        single_html = build_single_cluster_html(
            c_ip, cluster_time_lines, ntp_lines,
            status_data, battery_data, rw_data, disk_data, nic_data,
            quota_table_html, nfs_table_html, smb_table_html, audit_panel
        )

        cluster_results[c_ip] = {'html': single_html}

        print(f"[DEBUG] Disconnecting from {c_ip}")
        disc = disconnect_isilon_cluster(c_ip)
        print("\n".join(disc))

    if not cluster_results:
        print("No successful clusters connected. Exiting multi-cluster mode.")
        return

    # Build combined HTML
    print("[DEBUG] Building combined multi-cluster HTML...")
    combined_html = build_multi_cluster_html(cluster_results)

    multi_report_file = os.path.join(REPORT_FOLDER, f"{timestamp_str} - MultiCluster_IsilonDashboard.html")
    os.makedirs(os.path.dirname(multi_report_file), exist_ok=True)
    with open(multi_report_file, "w", encoding="utf-8") as f:
        f.write(combined_html)

    print(f"\nMulti-Cluster HTML Dashboard saved to: {multi_report_file}")

    choice = input("Send this multi-cluster HTML via email? (y/n): ").strip().lower()
    if choice.startswith("y"):
        send_html_via_email(multi_report_file)
        print("Email sent (if no exceptions).")

def main():
    print("\n=== ENHANCED ISILON SCRIPT (with Time/NTP, Quota, JSON parsing, AuditRate,"
          " auto-upload, Multi-Cluster, Timeouts, and Debug Prints) ===")

    multi_answer = input("Do you want to monitor multiple clusters? (y/n): ").strip().lower()
    if multi_answer.startswith("y"):
        while True:
            try:
                num_clusters = int(input("How many clusters do you want to monitor? Enter a number: ").strip())
                if num_clusters < 2:
                    print("Please enter at least 2 if you want multiple. Or press Ctrl+C to exit.")
                    continue
                break
            except ValueError:
                print("Invalid number. Please try again.")
        handle_multiple_clusters_mode(num_clusters)
    else:
        cluster_ip = input("Enter the Isilon cluster IP/Hostname: ").strip()
        user_name  = input("Enter your username: ").strip()
        password   = getpass.getpass("Enter your password: ")

        connect_result = connect_isilon_cluster(cluster_ip, user_name, password)
        for line in connect_result:
            print(line)

        if any("ERROR" in x for x in connect_result):
            return

        menu_loop(cluster_ip)
        print("Finished. (If you did not 'd' to disconnect, SSH remains open.)")

def send_html_via_email(filepath: str):
    if not os.path.isfile(filepath):
        print("HTML file not found for emailing.")
        return
    with open(filepath, "r", encoding="utf-8") as f:
        html_data = f.read()

    msg = MIMEMultipart("alternative")
    msg["Subject"] = MAIL_SUBJECT
    msg["From"] = MAIL_FROM
    msg["To"] = MAIL_TO
    if MAIL_CC:
        msg["CC"] = MAIL_CC

    part_html = MIMEText(html_data, "html")
    msg.attach(part_html)

    with open(filepath, "rb") as af:
        part_file = MIMEBase("application", "octet-stream")
        part_file.set_payload(af.read())
    encoders.encode_base64(part_file)
    part_file.add_header(
        "Content-Disposition",
        f'attachment; filename="{os.path.basename(filepath)}"'
    )
    msg.attach(part_file)

    try:
        with smtplib.SMTP(MAIL_SERVER, MAIL_PORT) as server:
            # server.starttls()
            # server.login("user","pass")
            server.send_message(msg)
        print("Email successfully sent.")
    except Exception as ex:
        print(f"Error sending email: {ex}")


if __name__ == "__main__":
    main()

# OneFS-Dashboard

Use at your own risk.
This is part of testing tools i build

https://buymeacoffee.com/vallentes


![image](https://github.com/user-attachments/assets/15406cf7-86f5-469f-b2d2-6923e67c0517)
![image](https://github.com/user-attachments/assets/da686cbf-ce6b-414b-b043-941a9f156af1)





Summary of This Enhanced Isilon Python Script

Connect to One or Multiple Clusters

The script can prompt you to connect to a single Isilon cluster or multiple clusters. For each cluster, you supply IP/hostname, username, and password. The script establishes an SSH session, uploads a custom audit script if needed, and then gathers various configuration and status details.

Retrieve and Display Key Isilon Details

Status (e.g., isi status)

Battery Status (isi batterystatus list)

Read/Write Mode (isi readonly list)

Disk Usage (via isi_for_array -s df -ik)

NIC Info (isi network interfaces list)

Quota Usage (isi quota quotas list --format json)

NFS Exports (isi nfs exports list --format json)

SMB Shares (isi smb share list --format json)

Time & NTP Info (via isi_for_array -s date and isi ntp servers list)

Audit Rate (via a custom auditrates.sh script uploaded to /root/)

Generate a Modern HTML Dashboard

Bootstrap Accordion: Each area (e.g., disk usage, NICs, NFS, SMB, etc.) is in a collapsible panel.

IDs are Sanitized: So cluster names with dots (e.g., 10.X.X.71) don’t break collapsible sections.

Exports JSON Parsing: NFS, SMB, and Quota data are shown in tables for readability.

Multi-Cluster Support

If you choose to monitor multiple clusters, the script repeats these steps for each cluster and then combines their dashboards into one multi-cluster HTML.

Each cluster’s dashboard is embedded in its own accordion panel.

Email the Dashboard (Optional)

After generating the HTML file, you can decide whether to send it via email. The script attaches the HTML as an email with a subject indicating the date/time.


How to Obtain the Dashboard


Run the Script
Execute the Python script (python <scriptname>.py).

Choose Single or Multi-Cluster

If single, the script connects to just one cluster and shows a menu for various commands.

If multi, you specify the number of clusters to monitor and provide credentials for each.

Select “Generate HTML Dashboard (option 6)”

In the script’s menu, choose the dashboard option. The script then:

Uploads the auditrates.sh script (if not already done)

Gathers all cluster data (status, battery, disk, etc.)

Builds the HTML using Bootstrap.

Check the Output

The resulting HTML file is saved (e.g., in C:\Users\ZX\Desktop\MY SCRIPTS\...).

If you select “y” for email, the dashboard is also sent to your email address.

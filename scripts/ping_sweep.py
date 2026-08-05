import os
import subprocess
import threading
import sys
from queue import Queue

def ping_ip(ip, active_ips):
    # Use ping -c 1 -W 1 (wait 1 second)
    res = subprocess.run(['ping', '-c', '1', '-W', '1', ip], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if res.returncode == 0:
        active_ips.append(ip)

def worker(queue, active_ips):
    while True:
        ip = queue.get()
        if ip is None:
            break
        ping_ip(ip, active_ips)
        queue.task_done()

def main():
    active_ips = []
    queue = Queue()
    threads = []
    num_threads = 50

    for i in range(num_threads):
        t = threading.Thread(target=worker, args=(queue, active_ips))
        t.start()
        threads.append(t)

    subnet = os.environ.get("PING_SWEEP_SUBNET", "192.0.2")
    for i in range(1, 255):
        queue.put(f"{subnet}.{i}")

    queue.join()

    for _ in range(num_threads):
        queue.put(None)
    for t in threads:
        t.join()

    active_ips.sort(key=lambda ip: list(map(int, ip.split('.'))))
    print("Active IPs:")
    for ip in active_ips:
        print(f"  {ip}")

if __name__ == "__main__":
    main()

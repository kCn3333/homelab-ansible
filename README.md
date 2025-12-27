# ⚙️ Homelab Ansible Automation

A collection of Ansible playbooks for managing my homelab cluster infrastructure through [Semaphore](https://www.semaphoreui.com/) UI.

## 📋 What's Inside

### Core Operations
- **Wake-on-LAN** - Power on nodes remotely using magic packets (pure Python implementation, no external dependencies)
- **Shutdown** - Gracefully power down nodes with Docker cleanup
- **Reboot** - Restart nodes and wait for them to come back online
- **Health Check** - Monitor system resources, uptime, and Docker status

### Maintenance
- **System Updates** - Update packages on Debian/Ubuntu systems
- **Disk Cleanup** - Free up disk space by removing old logs and Docker artifacts
- **DNS Fix** - Repair DNS configuration (useful after MAAS installations)
- **MAAS Cleanup** - Remove MAAS controller and cloud-init remnants

### Docker Management
- **Container Operations** - Start, stop, restart containers
- **Compose Deployments** - Deploy and manage docker-compose stacks
- **Image Updates** - Pull latest images and prune unused resources

## 🚀 Getting Started

### Prerequisites
- Semaphore installed and running
- SSH access to your nodes
- Sudo privileges configured on target hosts

### Setup in Semaphore

1. **Add Repository**
   - Import this Git repository in Semaphore
   - Configure SSH key if using private repo

2. **Create Inventory**
   - Define your hosts (master, workers, etc.)
   - Set `ansible_user` and `ansible_become: true`
   - Add `mac_address` for nodes that support Wake-on-LAN

3. **Create Task Templates**
   - Create templates for playbooks you want to use
   - Configure variables as needed
   - Set appropriate timeouts for long-running tasks

## 📝 Important Notes

- **Pure Python WoL** - No need to install `wakeonlan` or other tools, uses built-in Python
- **Idempotent** - Safe to run playbooks multiple times
- **Graceful handling** - Playbooks handle offline nodes without failing

## 🔧 Configuration

All playbooks use variables that can be overridden in Semaphore's "Extra Variables" field:

```json
{
  "shutdown_delay": 5,
  "upgrade_type": "dist",
  "wol_timeout": 120
}
```

Check individual playbook headers for available variables.

## 📄 License

MIT License - Use freely for your own homelab automation.

---

**Built for Semaphore** | **Tested on Debian/Ubuntu** | **K3s Ready**
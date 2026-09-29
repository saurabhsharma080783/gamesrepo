// Deploys the assessment appliance VM into the customer's subscription from our image.
//
//   az deployment group create -g <resource-group> -f main.bicep -p main.parameters.json
//
// The VM has no public IP: reach it through Azure Bastion or the customer's VPN. It has a
// system-assigned identity allowed to deallocate this VM only (used by assess-run --deallocate
// and the idle timer), and a daily auto-shutdown schedule as a backstop against forgotten VMs.
// Deploying needs Contributor plus User Access Administrator (or Owner) on the resource group,
// because of the role assignment.

@description('VM name.')
param vmName string = 'vm-azure-assess'

param location string = resourceGroup().location

@description('CPU-only is enough: about 25 minutes per report on 4 vCPUs, less on 8-16.')
param vmSize string = 'Standard_D16s_v5'

@description('Resource ID of the appliance image: a gallery image version or a managed image.')
param imageId string

@description('Existing subnet for the VM (for example a spoke subnet reachable from Bastion or the VPN).')
param subnetId string

param adminUsername string = 'assessor'

@secure()
@description('SSH public key for the admin user (password login is disabled).')
param sshPublicKey string

@allowed(['StandardSSD_LRS', 'Premium_LRS', 'Standard_LRS'])
@description('While the VM is deallocated only the disk is billed; StandardSSD keeps that low.')
param osDiskType string = 'StandardSSD_LRS'

param osDiskSizeGB int = 64

@description('Spot pricing: much cheaper, but Azure may evict the VM (it is deallocated, not deleted; rerun the report).')
param useSpot bool = false

@description('Daily auto-shutdown (deallocate) time, HHmm, as a backstop. Empty to disable.')
param autoShutdownTime string = '1900'

@description('Windows time zone ID for the auto-shutdown time, e.g. "UTC", "GMT Standard Time", "India Standard Time".')
param autoShutdownTimeZone string = 'UTC'

@description('Minutes with nobody logged in and no report running before the VM deallocates itself. 0 disables.')
@minValue(0)
param idleMinutes int = 60

@description('Source address range allowed to SSH to the VM (e.g. the Bastion subnet or VPN range).')
param sshSourcePrefix string = 'VirtualNetwork'

param tags object = {
  workload: 'azure-assessment'
}

var virtualMachineContributor = subscriptionResourceId('Microsoft.Authorization/roleDefinitions',
  '9980e02c-c2be-4d73-94e8-173b1dc7cf3c')

var cloudInit = format('''#cloud-config
runcmd:
  - usermod -aG assess {0}
  - sed -i 's/^ASSESS_IDLE_MINUTES=.*/ASSESS_IDLE_MINUTES={1}/' /etc/azure-assess/idle.env
''', adminUsername, idleMinutes)

resource nsg 'Microsoft.Network/networkSecurityGroups@2024-05-01' = {
  name: '${vmName}-nsg'
  location: location
  tags: tags
  properties: {
    securityRules: [
      {
        name: 'allow-ssh-internal'
        properties: {
          priority: 100
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourceAddressPrefix: sshSourcePrefix
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '22'
        }
      }
    ]
  }
}

resource nic 'Microsoft.Network/networkInterfaces@2024-05-01' = {
  name: '${vmName}-nic'
  location: location
  tags: tags
  properties: {
    networkSecurityGroup: { id: nsg.id }
    ipConfigurations: [
      {
        name: 'ipconfig1'
        properties: {
          subnet: { id: subnetId }
          privateIPAllocationMethod: 'Dynamic'
        }
      }
    ]
  }
}

resource vm 'Microsoft.Compute/virtualMachines@2024-07-01' = {
  name: vmName
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: {
    hardwareProfile: { vmSize: vmSize }
    priority: useSpot ? 'Spot' : 'Regular'
    evictionPolicy: useSpot ? 'Deallocate' : null
    billingProfile: useSpot ? { maxPrice: -1 } : null
    storageProfile: {
      imageReference: { id: imageId }
      osDisk: {
        createOption: 'FromImage'
        diskSizeGB: osDiskSizeGB
        managedDisk: { storageAccountType: osDiskType }
        deleteOption: 'Delete'
      }
    }
    osProfile: {
      computerName: vmName
      adminUsername: adminUsername
      customData: base64(cloudInit)
      linuxConfiguration: {
        disablePasswordAuthentication: true
        ssh: {
          publicKeys: [
            {
              path: '/home/${adminUsername}/.ssh/authorized_keys'
              keyData: sshPublicKey
            }
          ]
        }
      }
    }
    networkProfile: {
      networkInterfaces: [
        {
          id: nic.id
          properties: { deleteOption: 'Delete' }
        }
      ]
    }
    diagnosticsProfile: {
      bootDiagnostics: { enabled: true }
    }
  }
}

// Lets the VM deallocate itself (assess-run --deallocate, idle timer). Scoped to this VM only.
resource selfDeallocate 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(vm.id, virtualMachineContributor)
  scope: vm
  properties: {
    roleDefinitionId: virtualMachineContributor
    principalId: vm.identity.principalId
    principalType: 'ServicePrincipal'
    description: 'azure-assess appliance: deallocate itself when a report finishes or when idle'
  }
}

resource autoShutdown 'Microsoft.DevTestLab/schedules@2018-09-15' = if (!empty(autoShutdownTime)) {
  name: 'shutdown-computevm-${vmName}'
  location: location
  tags: tags
  properties: {
    status: 'Enabled'
    taskType: 'ComputeVmShutdownTask'
    dailyRecurrence: { time: autoShutdownTime }
    timeZoneId: autoShutdownTimeZone
    targetResourceId: vm.id
    notificationSettings: { status: 'Disabled' }
  }
}

output vmId string = vm.id
output privateIp string = nic.properties.ipConfigurations[0].properties.privateIPAddress
output connect string = 'az network bastion ssh --name <bastion> --resource-group <bastion-rg> --target-resource-id ${vm.id} --auth-type ssh-key --username ${adminUsername} --ssh-key <private-key>'

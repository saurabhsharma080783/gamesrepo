<#
.SYNOPSIS
  Export Azure inventory for the assessment (run by the customer).
.DESCRIPTION
  Needs the Az.ResourceGraph module (Install-Module Az.ResourceGraph) and Reader access.
  Sign in first with Connect-AzAccount. Writes a single inventory.json.
.EXAMPLE
  ./Export-Inventory.ps1
  ./Export-Inventory.ps1 -SubscriptionId 1111...,2222...
#>
param(
  [string[]] $SubscriptionId,
  [string] $OutFile = "inventory.json"
)
$ErrorActionPreference = "Stop"

$query = @"
Resources
| project id, name, type, location, resourceGroup, subscriptionId, kind, sku, tags, zones, properties
| join kind=leftouter (ResourceContainers | where type =~ 'microsoft.resources/subscriptions'
    | project subscriptionId, subscriptionName = name) on subscriptionId
| project-away subscriptionId1
| order by id asc
"@

$all = [System.Collections.Generic.List[object]]::new()
$skipToken = $null
do {
  $params = @{ Query = $query; First = 1000 }
  if ($SubscriptionId) { $params.Subscription = $SubscriptionId } else { $params.UseTenantScope = $true }
  if ($skipToken) { $params.SkipToken = $skipToken }
  $page = Search-AzGraph @params
  # Az.ResourceGraph 0.11+ returns rows under .Data; older versions return the rows directly.
  $rows = if ($page.PSObject.Properties['Data']) { $page.Data } else { $page }
  foreach ($r in $rows) { $all.Add($r) }
  $skipToken = $page.SkipToken
  Write-Host "Fetched $($all.Count) resources..."
} while ($skipToken)

@{ collected_at = (Get-Date).ToUniversalTime().ToString("s") + "Z"; resources = $all } |
  ConvertTo-Json -Depth 100 | Set-Content -Path $OutFile -Encoding utf8
Write-Host "Done. Please share $OutFile ($($all.Count) resources)."

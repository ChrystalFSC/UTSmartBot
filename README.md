# UTSmartBot

## Overview
UTSmartBot is a smart bot application deployed on Azure Web App.

## Deployment

### Automatic Deployment
The application is set up for continuous deployment using GitHub Actions. Any push to the `master` branch will trigger the deployment workflow.

### Manual Deployment
To deploy manually, follow these steps:
1. Install Azure CLI and login:
   ```sh
   az login

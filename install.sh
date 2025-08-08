#!/bin/bash

# --- Configuration ---
REPO_URL="https://github.com/ExPLoSiVe1988/cloudflare-telegram-bot.git"
REPO_DIR="cloudflare-telegram-bot"
VENV_DIR="venv"
PM2_APP_NAME="cfbot"

# --- Colors ---
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# --- Helper Functions ---

# Print a formatted header
print_header() {
  clear
  echo -e "${GREEN}############################################################${NC}"
  echo -e "${GREEN}##                                                        ##${NC}"
  echo -e "${GREEN} ##           WELCOME TO Cloudflare-Telegram-Bot         ##${NC}"
  echo -e "${GREEN}  ##                 Installation Script                ##${NC}"
  echo -e "${GREEN} ##         Powered by @H_ExPLoSiVe(ExPLoSiVe1988)       ##${NC}"
  echo -e "${GREEN}##                                                        ##${NC}"
  echo -e "${GREEN}############################################################${NC}"
  echo ""
}

# Check for essential system packages and install if missing
check_requirements() {
  echo -e "${YELLOW}>>> Checking system dependencies...${NC}"
  local needs_install=0
  for cmd in git python3 python3-pip npm; do
    if ! command -v $cmd &> /dev/null; then
      echo -e "${YELLOW}Dependency '$cmd' not found. Marking for installation.${NC}"
      needs_install=1
    fi
  done

  if [ $needs_install -eq 1 ]; then
    echo -e "${YELLOW}Updating package lists and installing required packages...${NC}"
    sudo apt-get update
    sudo apt-get install -y git python3 python3-pip python3-venv nodejs npm
  fi

  if ! command -v pm2 &> /dev/null; then
    echo -e "${YELLOW}PM2 not found. Installing globally via npm...${NC}"
    sudo npm install -g pm2
  fi
  echo -e "${GREEN}System dependencies are satisfied.${NC}"
}

# Get user input for .env file
get_user_input() {
  local env_file_path=$1
  echo -e "\n${YELLOW}Please provide the following information:${NC}"
  read -rp "Cloudflare API Token (CF_API_TOKEN): " CF_API_TOKEN
  read -rp "Telegram Bot Token (TELEGRAM_BOT_TOKEN): " TELEGRAM_BOT_TOKEN
  read -rp "Telegram Admin ID (TELEGRAM_ADMIN_ID): " TELEGRAM_ADMIN_ID

  echo -e "\n${YELLOW}Saving information to $env_file_path...${NC}"
  cat > "$env_file_path" <<EOF
CF_API_TOKEN=$CF_API_TOKEN
TELEGRAM_BOT_TOKEN=$TELEGRAM_BOT_TOKEN
TELEGRAM_ADMIN_ID=$TELEGRAM_ADMIN_ID
EOF
  chmod 600 "$env_file_path"
  echo -e "${GREEN}.env file created and secured.${NC}"
}

# Send a notification message to Telegram
send_telegram_notification() {
  local message=$1
  # Robustly read variables from .env file
  if [ -f ".env" ]; then
    local token=$(grep "TELEGRAM_BOT_TOKEN" .env | cut -d '=' -f2)
    local admin_id=$(grep "TELEGRAM_ADMIN_ID" .env | cut -d '=' -f2)
    if [ -n "$token" ] && [ -n "$admin_id" ]; then
      curl -s -X POST "https://api.telegram.org/bot$token/sendMessage" \
        -d chat_id="$admin_id" \
        -d text="$message" > /dev/null
      echo -e "\n${GREEN}Notification sent to Telegram.${NC}"
    fi
  fi
}

# --- Main Functions ---

# Install the bot
install_bot() {
  print_header
  echo -e "${YELLOW}Starting bot installation...${NC}"
  if [ -d "$REPO_DIR" ]; then
    echo -e "${RED}❌ Bot folder already exists. Please use the 'Update' option or remove it first.${NC}"
    return
  fi

  echo -e "${YELLOW}Cloning repository from GitHub...${NC}"
  git clone "$REPO_URL"
  cd "$REPO_DIR" || { echo -e "${RED}Failed to enter repository directory. Aborting.${NC}"; exit 1; }

  get_user_input ".env"

  echo -e "${YELLOW}Creating Python virtual environment...${NC}"
  python3 -m venv "$VENV_DIR"

  echo -e "${YELLOW}Installing dependencies from requirements.txt...${NC}"
  "$VENV_DIR/bin/pip3" install -r requirements.txt

  echo -e "${YELLOW}Starting bot with PM2...${NC}"
  # Use the python interpreter from the virtual environment
  pm2 start bot.py --interpreter "$VENV_DIR/bin/python3" --name "$PM2_APP_NAME" --update-env

  echo -e "${YELLOW}Saving PM2 process list to run on startup...${NC}"
  pm2 save

  echo -e "\n${GREEN}✅ Bot installed and started successfully!${NC}"
  echo -e "${YELLOW}To make the bot run automatically after a reboot, please run the command that PM2 suggests below:${NC}"
  pm2 startup

  echo -e "\n📜 ${YELLOW}View logs:${NC} pm2 logs $PM2_APP_NAME"
  send_telegram_notification "🚀 Cloudflare bot installed and running successfully."
}

# Update the bot
update_bot() {
  print_header
  echo -e "${YELLOW}Starting bot update...${NC}"
  if [ ! -d "$REPO_DIR" ]; then
    echo -e "${RED}❌ Bot is not installed. Please use the 'Install' option first.${NC}"
    return
  fi
  cd "$REPO_DIR" || exit

  echo -e "${YELLOW}Pulling latest changes from GitHub...${NC}"
  git pull origin main

  echo -e "${YELLOW}Installing/updating dependencies...${NC}"
  "$VENV_DIR/bin/pip3" install -r requirements.txt --upgrade

  read -rp "Do you want to update your API tokens or Admin ID? (y/n): " answer
  if [[ $answer == [Yy]* ]]; then
    get_user_input ".env"
  fi

  echo -e "${YELLOW}Restarting bot with PM2...${NC}"
  pm2 restart "$PM2_APP_NAME" --update-env

  echo -e "\n${GREEN}✅ Bot updated successfully.${NC}"
  send_telegram_notification "✅ Cloudflare bot updated to the latest version. 🔄"
}

# Remove the bot
remove_bot() {
  print_header
  echo -e "${YELLOW}Removing bot completely...${NC}"
  pm2 delete "$PM2_APP_NAME" &> /dev/null || echo -e "${YELLOW}⚠️ Bot was not running or already removed from PM2.${NC}"
  pm2 save --force

  if [ -d "$REPO_DIR" ]; then
    rm -rf "$REPO_DIR"
    echo -e "${GREEN}🗑️ Bot directory ($REPO_DIR) has been removed.${NC}"
  else
    echo -e "${YELLOW}⚠️ Bot directory does not exist.${NC}"
  fi
  echo -e "\n${GREEN}✅ Bot removal process finished.${NC}"
}

# Main menu loop
main_menu() {
  while true; do
    echo -e "\n${YELLOW}Choose an option:${NC}"
    echo "1) Install Bot"
    echo "2) Update Bot"
    echo "3) Delete Bot"
    echo "4) Exit"
    read -rp "Choice: " choice
    case $choice in
      1) install_bot; break ;;
      2) update_bot; break ;;
      3) remove_bot; break ;;
      4) echo -e "${GREEN}👋 Exiting script.${NC}"; exit 0 ;;
      *) echo -e "${RED}❌ Invalid option! Please enter a number between 1 and 4.${NC}" ;;
    esac
  done
}

# --- Script Execution ---
print_header
check_requirements
main_menu

#!/bin/bash

REPO_DIR="cloudflare-telegram-bot"

SCRIPT_NAME=""
# Print header
print_header() {
  clear
  echo "############################################################"
echo "##                                                        ##"
echo " ##           WELCOME TO Cloudflare-Telegram-Bot         ##"
echo "  ##             Script Executed Successfully           ##"
echo " ##         Powered by @H_ExPLoSiVe(ExPLoSiVe1988)       ##"
echo "##                                                        ##"
echo "############################################################"
echo ""
}

# Check and install requirements if missing
check_requirements() {
  if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 is not installed. Installing python3..."
    sudo apt-get update
    sudo apt-get install -y python3
  fi

  if ! command -v pip3 >/dev/null 2>&1; then
    echo "pip3 is not installed. Installing pip3..."
    sudo apt-get update
    sudo apt-get install -y python3-pip
  fi

  if ! command -v npm >/dev/null 2>&1; then
    echo "npm is not installed. Installing nodejs and npm..."
    sudo apt-get update
    sudo apt-get install -y nodejs npm
  fi

  if ! command -v pm2 >/dev/null 2>&1; then
    echo "pm2 is not installed. Installing pm2..."
    sudo npm install -g pm2
  fi
}

# Get user input and save to .env
get_user_input() {
  echo "Please enter the following information:"

  read -rp "Cloudflare API Token (CF_API_TOKEN): " CF_API_TOKEN
  read -rp "Token Name (CF_TOKEN_NAME): " CF_TOKEN_NAME
  read -rp "Telegram Bot Token (TELEGRAM_BOT_TOKEN): " TELEGRAM_BOT_TOKEN
  read -rp "Telegram Admin ID (TELEGRAM_ADMIN_ID): " TELEGRAM_ADMIN_ID

  echo
  echo "Saving information to .env file ..."
  cat > .env <<EOF
CF_API_TOKEN=$CF_API_TOKEN
CF_TOKEN_NAME=$CF_TOKEN_NAME
TELEGRAM_BOT_TOKEN=$TELEGRAM_BOT_TOKEN
TELEGRAM_ADMIN_ID=$TELEGRAM_ADMIN_ID
EOF
  echo ".env file created."
}

# Install bot
install_bot() {
  print_header

  echo "Installing bot..."

  get_user_input

  if [ -d "$REPO_DIR" ]; then
    echo "Bot folder exists, updating..."
    cd "$REPO_DIR" || exit
    git pull origin main
  else
    echo "Cloning bot from GitHub..."
    git clone https://github.com/ExPLoSiVe1988/cloudflare-telegram-bot.git
    cd "$REPO_DIR" || exit
  fi

  echo "Installing dependencies..."
  pip3 install -r requirements.txt

  cp ../.env .

  chmod 600 .env

  echo "Starting bot with pm2..."
  pm2 start bot.py --interpreter python3 --watch --name cfbot --update-env

  echo "✅ Bot installed and started successfully."
  echo "📜 View logs: pm2 logs cfbot"

  source .env
  curl -s -X POST "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/sendMessage" \
    -d chat_id="$TELEGRAM_ADMIN_ID" \
    -d text='🚀 Cloudflare bot installed and running successfully.'
}

# Update bot
update_bot() {
  print_header
  echo "Updating bot..."

  if [ ! -d "$REPO_DIR" ]; then
    echo "❌ Bot is not installed. Please install first."
    return
  fi

  cd "$REPO_DIR" || exit
  git pull origin main
  pip3 install -r requirements.txt

  echo -n "Do you want to update connection info? (y/n): "
  read answer
  if [[ $answer == [Yy]* ]]; then
    get_user_input
    cp ../.env .
    chmod 600 .env
  fi

  pm2 restart cfbot --update-env

  echo "✅ Bot updated successfully."

  if [ -f ".env" ]; then
    source .env
    curl -s -X POST "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/sendMessage" \
      -d chat_id="$TELEGRAM_ADMIN_ID" \
      -d text='✅ Cloudflare bot updated to latest version. 🔄'
  fi
}

# Remove bot
remove_bot() {
  print_header
  echo "Removing bot completely..."

  # Stop and delete the PM2 process
  pm2 delete cfbot 2>/dev/null || echo "⚠️ Bot was not running or already removed."

  # If inside the cloudflare-telegram-bot folder, go up one level
  if [ "$(basename "$PWD")" = "cloudflare-telegram-bot" ]; then
    cd ..
  fi

  # Remove bot folder
  if [ -d "cloudflare-telegram-bot" ]; then
    rm -rf cloudflare-telegram-bot/
    echo "🗑 Bot folder removed."
  else
    echo "⚠️ Bot folder does not exist."
  fi

  # Remove .env file
  if [ -f ".env" ]; then
    rm -f .env
    echo "🗑 .env file removed."
  else
    echo "⚠️ .env file does not exist."
  fi

  echo "✅ Bot removed successfully."
}

# Main menu
print_menu() {
  echo
  echo "Choose one option:"
  echo "1) Install Bot"
  echo "2) Update Bot"
  echo "3) Delete Bot"
  echo "4) Exit"
  echo -n "Choice: "
}

# Run program
print_header
check_requirements

while true; do
  print_menu
  read choice
  case $choice in
    1)
      install_bot
      ;;
    2)
      update_bot
      ;;
    3)
      remove_bot
      ;;
    4)
      echo "👋 Exiting script."
      exit 0
      ;;
    *)
      echo "❌ Invalid option! Please enter a number between 1 and 4."
      ;;
  esac
done

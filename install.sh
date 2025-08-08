#!/bin/bash

# --- Configuration ---
PROJECT_DIR="cloudflare-telegram-bot" 
IMAGE_NAME="explosive1988/cfbot:latest" 

# --- Colors ---
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# --- Helper Functions ---
print_header() {
    clear
    echo -e "${GREEN}############################################################${NC}"
    echo -e "${GREEN}##                                                        ##${NC}"
    echo -e "${GREEN} ##        Cloudflare-Telegram-Bot Docker Installer      ##${NC}"
    echo -e "${GREEN}  ##               Powered by @H_ExPLoSiVe              ##${NC}"
    echo -e "${GREEN}##                                                        ##${NC}"
    echo -e "${GREEN}############################################################${NC}"
    echo ""
}

check_docker() {
    echo -e "${YELLOW}>>> Checking for Docker and Docker Compose...${NC}"
    if ! command -v docker &> /dev/null; then
        echo "Docker not found. Attempting to install..."
        sudo apt-get update -y && sudo apt-get install -y curl
        curl -fsSL https://get.docker.com -o get-docker.sh
        sudo sh get-docker.sh
        sudo usermod -aG docker $USER
        echo -e "${GREEN}Docker installed successfully. You might need to log out and log back in.${NC}"
    else
        echo -e "${GREEN}Docker is already installed.${NC}"
    fi

    if ! command -v docker-compose &> /dev/null; then
        echo "Docker Compose not found. Attempting to install..."
        sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
        sudo chmod +x /usr/local/bin/docker-compose
        echo -e "${GREEN}Docker Compose installed successfully.${NC}"
    else
        echo -e "${GREEN}Docker Compose is already installed.${NC}"
    fi
}

get_user_input() {
    echo -e "\n${YELLOW}Please provide the following information:${NC}"
    read -rp "Cloudflare API Token (CF_API_TOKEN): " CF_API_TOKEN
    read -rp "Telegram Bot Token (TELEGRAM_BOT_TOKEN): " TELEGRAM_BOT_TOKEN
    read -rp "Telegram Admin ID (TELEGRAM_ADMIN_ID): " TELEGRAM_ADMIN_ID
    
    echo -e "\n${YELLOW}Creating .env file...${NC}"
    cat > ".env" <<EOF
CF_API_TOKEN=$CF_API_TOKEN
TELEGRAM_BOT_TOKEN=$TELEGRAM_BOT_TOKEN
TELEGRAM_ADMIN_ID=$TELEGRAM_ADMIN_ID
EOF
    echo -e "${GREEN}.env file created successfully.${NC}"
}

# --- Main Functions ---
install_bot() {
    print_header
    if [ -d "$PROJECT_DIR" ]; then
        echo -e "${RED}❌ Project directory '$PROJECT_DIR' already exists. Please use the update or remove options.${NC}"
        return
    fi
    
    echo -e "${YELLOW}Creating project directory at ~/$PROJECT_DIR...${NC}"
    mkdir -p "$HOME/$PROJECT_DIR"
    cd "$HOME/$PROJECT_DIR" || exit

    echo -e "${YELLOW}Creating docker-compose.yml file...${NC}"
    cat > "docker-compose.yml" <<EOF
services:
  cfbot:
    image: ${IMAGE_NAME}
    container_name: cfbot
    restart: unless-stopped
    env_file:
      - .env
    volumes:
      - ./backups:/app/backups
      - ./config.json:/app/config.json
EOF

    echo -e "${YELLOW}Creating default config.json file...${NC}"
    cat > "config.json" <<EOF
{
  "monitoring_interval_seconds": 60,
  "failover_targets": [],
  "rotation_targets": []
}
EOF

    get_user_input

    echo -e "${YELLOW}Pulling the latest bot image from Docker Hub and starting...${NC}"
    docker-compose up -d
    
    echo -e "\n${GREEN}✅ Bot installed and started successfully!${NC}"
    echo -e "${BLUE}Use 'View Logs' from the main script to check the status.${NC}"
}

update_bot() {
    print_header
    if [ ! -d "$HOME/$PROJECT_DIR" ]; then
        echo -e "${RED}❌ Bot is not installed. Please use the install option first.${NC}"
        return
    fi
    cd "$HOME/$PROJECT_DIR" || exit
    
    echo -e "${YELLOW}Pulling the latest bot image from Docker Hub...${NC}"
    docker-compose pull
    
    echo -e "${YELLOW}Restarting the bot with the new image...${NC}"
    docker-compose up -d
    
    echo -e "\n${GREEN}✅ Bot updated successfully!${NC}"
}

remove_bot() {
    print_header
    if [ ! -d "$HOME/$PROJECT_DIR" ]; then
        echo -e "${RED}❌ Bot is not installed.${NC}"
        return
    fi
    cd "$HOME/$PROJECT_DIR" || exit
    
    echo -e "${YELLOW}Stopping and removing Docker containers and images...${NC}"
    docker-compose down --rmi all -v
    
    cd ~
    rm -rf "$PROJECT_DIR"
    echo -e "\n${GREEN}✅ Bot and all associated data have been completely removed.${NC}"
}

view_logs() {
    print_header
    if [ ! -d "$HOME/$PROJECT_DIR" ]; then
        echo -e "${RED}❌ Bot is not installed.${NC}"
        return
    fi
    echo -e "${YELLOW}Showing live logs... (Press Ctrl+C to exit)${NC}"
    cd "$HOME/$PROJECT_DIR" || exit
    docker-compose logs -f
}

# --- Main Menu ---
main_menu() {
    while true; do
        echo -e "\n${BLUE}--- Cloudflare Bot Docker Manager ---${NC}"
        echo "1) Install Bot"
        echo "2) Update Bot"
        echo "3) View Live Logs"
        echo "4) Remove Bot Completely"
        echo "5) Exit"
        read -rp "Choose an option: " choice
        case $choice in
            1) install_bot; break ;;
            2) update_bot; break ;;
            3) view_logs; break ;;
            4) remove_bot; break ;;
            5) echo -e "${GREEN}👋 Exiting.${NC}"; exit 0 ;;
            *) echo -e "${RED}Invalid option. Please try again.${NC}" ;;
        esac
    done
}

# --- Script Execution ---
print_header
check_docker
main_menu

#!/bin/bash

# --- Configuration ---
REPO_URL="https://github.com/ExPLoSiVe1988/cloudflare-telegram-bot.git"
PROJECT_DIR="cloudflare-telegram-bot"
ENV_FILE=".env"

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

# Check for Docker and Docker Compose and install if missing
check_docker() {
    echo -e "${YELLOW}>>> Checking for Docker and Docker Compose...${NC}"
    if ! command -v docker &> /dev/null; then
        echo "Docker not found. Attempting to install..."
        sudo apt-get update
        sudo apt-get install -y apt-transport-https ca-certificates curl software-properties-common
        curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg
        echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
        sudo apt-get update
        sudo apt-get install -y docker-ce docker-ce-cli containerd.io
        sudo usermod -aG docker $USER
        echo -e "${GREEN}Docker installed successfully. You might need to log out and log back in for group changes to take effect.${NC}"
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
    cat > "$ENV_FILE" <<EOF
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
    echo -e "${YELLOW}Cloning repository...${NC}"
    git clone "$REPO_URL"
    cd "$PROJECT_DIR" || exit
    get_user_input
    echo -e "${YELLOW}Building and starting the bot with Docker Compose... This may take a few minutes.${NC}"
    docker-compose up --build -d
    echo -e "\n${GREEN}✅ Bot installed and started successfully!${NC}"
    echo -e "${BLUE}Use the 'View Logs' option in the menu to check the status.${NC}"
}

update_bot() {
    print_header
    if [ ! -d "$PROJECT_DIR" ]; then
        echo -e "${RED}❌ Bot is not installed. Please use the install option first.${NC}"
        return
    fi
    cd "$PROJECT_DIR" || exit
    echo -e "${YELLOW}Pulling latest changes from GitHub...${NC}"
    git pull origin main
    echo -e "${YELLOW}Re-building the Docker image and restarting the bot...${NC}"
    docker-compose up --build -d
    echo -e "\n${GREEN}✅ Bot updated successfully!${NC}"
}

remove_bot() {
    print_header
    if [ ! -d "$PROJECT_DIR" ]; then
        echo -e "${RED}❌ Bot is not installed.${NC}"
        return
    fi
    cd "$PROJECT_DIR" || exit
    echo -e "${YELLOW}Stopping and removing Docker containers, images, and volumes...${NC}"
    docker-compose down --rmi all -v
    cd ..
    rm -rf "$PROJECT_DIR"
    echo -e "\n${GREEN}✅ Bot and all associated data have been completely removed.${NC}"
}

view_logs() {
    print_header
    if [ ! -d "$PROJECT_DIR" ]; then
        echo -e "${RED}❌ Bot is not installed.${NC}"
        return
    fi
    echo -e "${YELLOW}Showing live logs... (Press Ctrl+C to exit)${NC}"
    cd "$PROJECT_DIR" || exit
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

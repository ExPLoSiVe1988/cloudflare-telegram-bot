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

# --- Stop script on any error ---
set -e

# --- Helper Functions ---
print_header() {
    clear
    echo -e "${GREEN}############################################################${NC}"
    echo -e "${GREEN}##                                                        ##${NC}"
    echo -e "${GREEN} ##              Cloudflare-Telegram-Bot                 ##${NC}"
    echo -e "${GREEN}  ##             Multi-Account Installer                ##${NC}"
    echo -e "${GREEN} ##       Powered by @H_ExPLoSiVe (ExPLoSiVe1988)        ##${NC}"
    echo -e "${GREEN}##                                                         ##${NC}"
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
        sudo usermod -aG docker "$USER"
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

read_env_var() {
    local var_name=$1
    if [ -f ".env" ]; then
        grep "^${var_name}=" ".env" | cut -d '=' -f2-
    fi
}

write_env_var() {
    local var_name=$1
    local new_value=$2
    if [ -f ".env" ]; then
        if grep -q "^${var_name}=" ".env"; then
            sed -i "/^${var_name}=/c\\${var_name}=${new_value}" ".env"
        else
            echo "${var_name}=${new_value}" >> ".env"
        fi
    fi
}

get_user_input() {
    echo -e "\n${YELLOW}--- Admin Configuration ---${NC}"
    local admin_ids=""
    while true; do
        echo -e -n "Enter a Telegram Admin ID (leave empty to finish): "
        read admin_id
        if [ -z "$admin_id" ]; then break; fi
        if [[ ! "$admin_id" =~ ^[0-9]+$ ]]; then echo -e "${RED}Invalid ID. Please enter numbers only.${NC}"; continue; fi
        if [ -z "$admin_ids" ]; then admin_ids="$admin_id"; else admin_ids="$admin_ids,$admin_id"; fi
    done
    if [ -z "$admin_ids" ]; then echo -e "${RED}❌ Error: At least one Admin ID is required. Aborting.${NC}"; exit 1; fi

    echo -e "\n${YELLOW}--- Cloudflare Account Configuration ---${NC}"
    local cf_accounts=""
    local count=1
    while true; do
        echo -e "${BLUE}Configuring Cloudflare Account #$count...${NC}"
        while true; do echo -e -n "Enter a nickname for this account (e.g., Personal, Work): "; read nickname; if [ -n "$nickname" ]; then break; fi; echo -e "${RED}Nickname cannot be empty.${NC}"; done
        while true; do echo -e -n "Enter the API Token for '$nickname': "; read token; if [ -n "$token" ]; then break; fi; echo -e "${RED}Token cannot be empty.${NC}"; done
        
        if [ -z "$cf_accounts" ]; then cf_accounts="$nickname:$token"; else cf_accounts="$cf_accounts,$nickname:$token"; fi
        
        echo -e -n "Add another Cloudflare account? (y/n): "
        read add_another
        if [[ $add_another != [Yy]* ]]; then break; fi
        count=$((count + 1))
    done
    if [ -z "$cf_accounts" ]; then echo -e "${RED}❌ Error: At least one Cloudflare Account is required. Aborting.${NC}"; exit 1; fi
    
    echo -e "\n${YELLOW}Creating .env file...${NC}"
    echo -e -n "Enter your Telegram Bot Token: "
    read TELEGRAM_BOT_TOKEN
    if [ -z "$TELEGRAM_BOT_TOKEN" ]; then echo -e "${RED}❌ Error: Telegram Bot Token is required. Aborting.${NC}"; exit 1; fi

    cat > ".env" <<EOF
# Comma-separated list of Telegram Admin IDs
TELEGRAM_ADMIN_IDS=${admin_ids}
# Comma-separated list of Cloudflare accounts in format: Nickname1:Token1,Nickname2:Token2
CF_ACCOUNTS=${cf_accounts}
# The main bot token
TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN}
EOF
    echo -e "${GREEN}.env file created successfully.${NC}"
}

# --- Main Functions ---
install_bot() {
    print_header
    if [ -d "$HOME/$PROJECT_DIR" ]; then
        echo -e -n "${YELLOW}⚠️ Project directory already exists. Do you want to remove it and re-install? (y/n): ${NC}"
        read re_install
        if [[ $re_install == [Yy]* ]]; then
            remove_bot
        else
            echo -e "${BLUE}Installation cancelled.${NC}"
            return
        fi
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
EOF

    get_user_input

    echo -e "${YELLOW}Pulling the latest bot image from Docker Hub and starting...${NC}"
    docker-compose up -d
    
    echo -e "\n${GREEN}✅ Bot installed and started successfully!${NC}"
    echo -e "${BLUE}Use 'View Logs' from the main script to check the status.${NC}"
}

update_bot() {
    print_header
    if [ ! -d "$HOME/$PROJECT_DIR" ]; then echo -e "${RED}❌ Bot is not installed. Please use the install option first.${NC}"; return; fi
    cd "$HOME/$PROJECT_DIR" || exit
    
    echo -e "${YELLOW}Pulling the latest bot image from Docker Hub...${NC}"
    docker-compose pull
    
    echo -e "${YELLOW}Recreating the container with the new image...${NC}"
    docker-compose up -d --force-recreate
    
    echo -e "\n${GREEN}✅ Bot code updated successfully!${NC}"
    
    echo -e -n "Do you want to edit your Admins or Cloudflare Accounts now? (y/n): "
    read edit_now
    if [[ $edit_now == [Yy]* ]]; then
        edit_config
    fi
}

remove_bot() {
    print_header
    if [ ! -d "$HOME/$PROJECT_DIR" ]; then echo -e "${RED}❌ Bot is not installed.${NC}"; return; fi
    cd "$HOME/$PROJECT_DIR" || exit
    
    echo -e "${YELLOW}Stopping and removing Docker containers and images...${NC}"
    if [ ! -f ".env" ]; then
        touch .env
    fi
    docker-compose down --rmi all -v
    
    cd ~
    rm -rf "$PROJECT_DIR"
    echo -e "\n${GREEN}✅ Bot and all associated data have been completely removed.${NC}"
}

view_logs() {
    print_header
    if [ ! -d "$HOME/$PROJECT_DIR" ]; then echo -e "${RED}❌ Bot is not installed.${NC}"; return; fi
    echo -e "${YELLOW}Showing live logs... (Press Ctrl+C to exit)${NC}"
    cd "$HOME/$PROJECT_DIR" || exit
    docker-compose logs -f
}

manage_admins() {
    local current_admins=$(read_env_var "TELEGRAM_ADMIN_IDS")
    echo -e "Current Admins: ${YELLOW}${current_admins:-None}${NC}"
    echo -e -n "1) Add Admin, 2) Remove Admin, 3) Back: "
    read choice
    case $choice in
        1)
            echo -e -n "Enter new Admin ID to add: "
            read new_admin
            if ! [[ "$new_admin" =~ ^[0-9]+$ ]]; then echo -e "${RED}Invalid ID.${NC}"; return; fi
            if [ -z "$current_admins" ]; then new_list="$new_admin"; else new_list="$current_admins,$new_admin"; fi
            write_env_var "TELEGRAM_ADMIN_IDS" "$new_list"
            echo -e "${GREEN}Admin added.${NC}"
            ;;
        2)
            echo -e -n "Enter Admin ID to remove: "
            read remove_admin
            if ! [[ "$remove_admin" =~ ^[0-9]+$ ]]; then echo -e "${RED}Invalid ID.${NC}"; return; fi
            new_list=$(echo "$current_admins" | tr ',' '\n' | grep -v "^$remove_admin$" | tr '\n' ',' | sed 's/,$//')
            write_env_var "TELEGRAM_ADMIN_IDS" "$new_list"
            echo -e "${GREEN}Admin removed.${NC}"
            ;;
        3) return;;
        *) echo -e "${RED}Invalid option.${NC}";;
    esac
}

manage_cf_accounts() {
    local current_accounts=$(read_env_var "CF_ACCOUNTS")
    echo "Current Cloudflare Accounts:"
    echo -e "${YELLOW}${current_accounts:-None}${NC}" | tr ',' '\n'
    echo -e -n "1) Add Account, 2) Remove Account (by nickname), 3) Back: "
    read choice
    case $choice in
        1)
            echo -e -n "Enter a nickname for the new account: "
            read new_nickname
            echo -e -n "Enter the API Token for '$new_nickname': "
            read new_token
            if [ -z "$new_nickname" ] || [ -z "$new_token" ]; then echo -e "${RED}Nickname and Token cannot be empty.${NC}"; return; fi
            new_entry="$new_nickname:$new_token"
            if [ -z "$current_accounts" ]; then new_list="$new_entry"; else new_list="$current_accounts,$new_entry"; fi
            write_env_var "CF_ACCOUNTS" "$new_list"
            echo -e "${GREEN}Account added.${NC}"
            ;;
        2)
            echo -e -n "Enter the nickname of the account to remove: "
            read remove_nickname
            if [ -z "$remove_nickname" ]; then echo -e "${RED}Nickname cannot be empty.${NC}"; return; fi
            new_list=$(echo "$current_accounts" | tr ',' '\n' | grep -v "^$remove_nickname:" | tr '\n' ',' | sed 's/,$//')
            write_env_var "CF_ACCOUNTS" "$new_list"
            echo -e "${GREEN}Account removed.${NC}"
            ;;
        3) return;;
        *) echo -e "${RED}Invalid option.${NC}";;
    esac
}

edit_config() {
    print_header
    if [ ! -d "$HOME/$PROJECT_DIR" ]; then echo -e "${RED}Bot is not installed.${NC}"; return; fi
    cd "$HOME/$PROJECT_DIR" || exit

    local config_changed=0
    while true; do
        echo -e "\n${BLUE}--- Edit Configuration ---${NC}"
        echo "1) Manage Admins"
        echo "2) Manage Cloudflare Accounts"
        echo "3) Edit Telegram Bot Token"
        echo "4) Done - Apply Changes and Restart"
        echo -e -n "Choose an option: "
        read choice
        case $choice in
            1) manage_admins; config_changed=1;;
            2) manage_cf_accounts; config_changed=1;;
            3)
                echo -e -n "Enter the new Telegram Bot Token: "
                read new_bot_token
                if [ -n "$new_bot_token" ]; then
                    write_env_var "TELEGRAM_BOT_TOKEN" "$new_bot_token"
                    echo -e "${GREEN}Bot Token updated.${NC}"
                    config_changed=1
                else
                    echo -e "${RED}Bot Token cannot be empty.${NC}"
                fi
                ;;
            4) break;;
            *) echo -e "${RED}Invalid option.${NC}";;
        esac
    done
    
    if [ "$config_changed" -eq 1 ]; then
        echo -e "${YELLOW}Recreating the container to apply changes...${NC}"
        docker-compose down
        docker-compose up -d
        echo -e "\n${GREEN}✅ Configuration updated and bot restarted!${NC}"
    else
        echo -e "${BLUE}No changes made. Bot was not restarted.${NC}"
    fi
}

# --- Main Menu ---
main_menu() {
    while true; do
        echo -e "\n${BLUE}--- Cloudflare Bot Docker Manager ---${NC}"
        echo "1) Install or Re-Install Bot"
        echo "2) Update Bot"
        echo "3) Edit Configuration"
        echo "4) View Live Logs"
        echo "5) Remove Bot Completely"
        echo "6) Exit"
        echo -e -n "Choose an option: "
        read choice
        case $choice in
            1) install_bot; break ;;
            2) update_bot; break ;;
            3) edit_config; break ;;
            4) view_logs; break ;;
            5) remove_bot; break ;;
            6) echo -e "${GREEN}👋 Exiting.${NC}"; exit 0 ;;
            *) echo -e "${RED}Invalid option. Please try again.${NC}" ;;
        esac
    done
}

# --- Script Execution ---
print_header
check_docker
main_menu

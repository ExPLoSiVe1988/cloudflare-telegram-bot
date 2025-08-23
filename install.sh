#!/bin/bash

# --- Colors for better output ---
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

# --- Core Variables ---
REPO_URL="https://github.com/ExPLoSiVe1988/cloudflare-telegram-bot.git"
PROJECT_DIR="cloudflare-telegram-bot"
ENV_FILE="$PROJECT_DIR/.env"
CONFIG_FILE="$PROJECT_DIR/config.json"

# Function to display a header
print_header() {
    clear
    echo -e "${GREEN}############################################################${NC}"
    echo -e "${GREEN}##                                                        ##${NC}"
    echo -e "${GREEN} ##              Cloudflare-Telegram-Bot                 ##${NC}"
    echo -e "${GREEN}  ##        Intelligent Failover & DNS Manager          ##${NC}"
    echo -e "${GREEN} ##       Powered by @H_ExPLoSiVe (ExPLoSiVe1988)        ##${NC}"
    echo -e "${GREEN}##                                                        ##${NC}"
    echo -e "${GREEN}############################################################${NC}"
    echo ""
}

read_env_var() {
    grep "^${1}=" "$ENV_FILE" | cut -d'=' -f2-
}

write_env_var() {
    if grep -q "^${1}=" "$ENV_FILE"; then
        sed -i "s|^${1}=.*|${1}=${2}|" "$ENV_FILE"
    else
        echo "${1}=${2}" >> "$ENV_FILE"
    fi
}

prompt_for_changes() {
    read -p "Apply these changes and restart the bot? (y/n): " confirm_restart
    if [[ "$confirm_restart" == "y" || "$confirm_restart" == "Y" ]]; then
        echo -e "${GREEN}Restarting the bot to apply changes...${NC}"
        cd "$PROJECT_DIR" && docker-compose restart && cd ..
        echo -e "${GREEN}Bot restarted successfully.${NC}"
    else
        echo -e "${YELLOW}Changes saved, but bot was not restarted. Please restart manually to apply.${NC}"
    fi
}

# --- Configuration Management Functions ---

manage_admins() {
    local current_admins=$(read_env_var "TELEGRAM_ADMIN_IDS")
    echo -e "\nCurrent Admins: ${YELLOW}${current_admins:-None}${NC}"
    echo "1) Add Admin ID"
    echo "2) Remove Admin ID"
    echo "3) Back"
    read -p "Choose an option: " choice
    case $choice in
        1)
            read -p "Enter new Admin ID to add: " new_admin
            if ! [[ "$new_admin" =~ ^[0-9]+$ ]]; then echo -e "${RED}Invalid ID. Must be a number.${NC}"; return; fi
            if [[ ",$current_admins," == *",$new_admin,"* ]]; then
                echo -e "${YELLOW}Admin ID $new_admin already exists.${NC}"
            else
                if [ -z "$current_admins" ]; then new_list="$new_admin"; else new_list="$current_admins,$new_admin"; fi
                write_env_var "TELEGRAM_ADMIN_IDS" "$new_list"
                echo -e "${GREEN}Admin $new_admin added.${NC}"
            fi
            ;;
        2)
            read -p "Enter Admin ID to remove: " remove_admin
            if ! [[ "$remove_admin" =~ ^[0-9]+$ ]]; then echo -e "${RED}Invalid ID. Must be a number.${NC}"; return; fi
            new_list=$(echo ",$current_admins," | sed "s/,$remove_admin,/,/" | sed 's/^,//;s/,$//')
            if [ "$new_list" == "$current_admins" ]; then
                echo -e "${RED}Admin ID $remove_admin not found.${NC}"
            else
                write_env_var "TELEGRAM_ADMIN_IDS" "$new_list"
                echo -e "${GREEN}Admin $remove_admin removed.${NC}"
            fi
            ;;
        3) return;;
        *) echo -e "${RED}Invalid option.${NC}";;
    esac
    read -p "Press Enter to continue..."
}

manage_cf_accounts() {
    while true; do
        print_header
        echo -e "${YELLOW}--- Manage Cloudflare Accounts ---${NC}"
        
        current_cf_accounts=$(read_env_var "CF_ACCOUNTS")
        echo "Current Accounts: $current_cf_accounts"
        echo ""
        echo "1) Add a new Cloudflare account"
        echo "2) Remove an existing Cloudflare account"
        echo "3) Back to previous menu"
        read -p "Choose an option: " cf_choice

        case $cf_choice in
            1)
                read -p "  Enter a Nickname for the new account: " cf_nickname
                read -p "  Enter the Cloudflare API Token for this account: " cf_token
                new_entry="$cf_nickname:$cf_token"
                if [ -z "$current_cf_accounts" ]; then
                    sed -i "s|^CF_ACCOUNTS=.*|CF_ACCOUNTS=$new_entry|" "$ENV_FILE"
                else
                    sed -i "s|^CF_ACCOUNTS=.*|CF_ACCOUNTS=$current_cf_accounts,$new_entry|" "$ENV_FILE"
                fi
                echo -e "${GREEN}Account '$cf_nickname' added.${NC}"
                read -p "Press Enter to continue..."
                ;;
            2)
                if [ -z "$current_cf_accounts" ]; then
                    echo -e "${YELLOW}No accounts to remove.${NC}"
                else
                    mapfile -t accounts < <(echo "$current_cf_accounts" | tr ',' '\n')
                    echo "Select an account to remove:"
                    i=1
                    for acc in "${accounts[@]}"; do
                        nickname=$(echo "$acc" | cut -d':' -f1)
                        echo "  $i) $nickname"
                        i=$((i+1))
                    done
                    read -p "Enter the number of the account to remove (or 0 to cancel): " del_choice
                    if [[ "$del_choice" -gt 0 && "$del_choice" -le ${#accounts[@]} ]]; then
                        unset "accounts[$((del_choice-1))]"
                        new_accounts_list=$(IFS=,; echo "${accounts[*]}")
                        sed -i "s|^CF_ACCOUNTS=.*|CF_ACCOUNTS=$new_accounts_list|" "$ENV_FILE"
                        echo -e "${GREEN}Account removed.${NC}"
                    else
                        echo "Removal cancelled."
                    fi
                fi
                read -p "Press Enter to continue..."
                ;;
            3)
                break
            *)
                echo -e "${RED}Invalid option.${NC}"
                read -p "Press Enter to continue..."
                ;;
        esac
    done
}

edit_config() {
    if [ ! -f "$ENV_FILE" ]; then
        echo -e "${RED}No .env file found. Please install the bot first.${NC}"
        read -p "Press Enter to continue..."
        return
    fi

    local config_changed=false
    while true; do
        print_header
        echo -e "${YELLOW}--- Edit Core Configuration (.env) ---${NC}"
        echo "1) Manage Admins"
        echo "2) Manage Cloudflare Accounts"
        echo "3) Edit Telegram Bot Token"
        echo "4) Back to Main Menu"
        read -p "Choose an option: " choice
        
        case $choice in
            1) manage_admins; config_changed=true;;
            2) manage_cf_accounts; config_changed=true;;
            3)
                current_token=$(read_env_var "TELEGRAM_BOT_TOKEN")
                echo "Current Token: $current_token"
                read -p "Enter the new Telegram Bot Token (leave empty to cancel): " new_bot_token
                if [ -n "$new_bot_token" ]; then
                    write_env_var "TELEGRAM_BOT_TOKEN" "$new_bot_token"
                    echo -e "${GREEN}Bot Token updated.${NC}"
                    config_changed=true
                else
                    echo -e "${YELLOW}No changes made.${NC}"
                fi
                read -p "Press Enter to continue..."
                ;;
            4) break;;
            *) echo -e "${RED}Invalid option.${NC}";;
        esac
    done

    if [ "$config_changed" = true ]; then
        prompt_for_changes
    fi
}


# --- Main Menu Functions ---

install_bot() {
    print_header
    echo -e "${GREEN}Starting Bot Installation...${NC}"

    if ! command -v git &> /dev/null || ! command -v curl &> /dev/null; then
        echo "Git or Curl not found. Installing required packages..."
        if command -v apt-get &> /dev/null; then sudo apt-get update && sudo apt-get install -y git curl; elif command -v yum &> /dev/null; then sudo yum install -y git curl; else echo -e "${RED}Error: Could not install Git/Curl.${NC}"; exit 1; fi
    fi

    if ! command -v docker &> /dev/null; then
        echo "Docker not found. Installing Docker..."; curl -fsSL https://get.docker.com -o get-docker.sh; sudo sh get-docker.sh; rm get-docker.sh;
    fi
    if ! command -v docker-compose &> /dev/null; then
        echo "Docker Compose not found. Installing..."; sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose; sudo chmod +x /usr/local/bin/docker-compose;
    fi

    if [ -d "$PROJECT_DIR" ]; then
        echo -e "${YELLOW}Project directory already exists. Skipping clone.${NC}";
    else
        git clone "$REPO_URL";
    fi
    cd "$PROJECT_DIR" || exit

    echo -e "${YELLOW}--- Core Configuration (.env) ---${NC}"
    read -p "Enter your Telegram Bot Token: " bot_token
    read -p "Enter your Telegram Admin User ID(s) (comma-separated): " admin_ids
    
    cf_accounts_list=""
    while true; do
        read -p "Add a Cloudflare account? (y/n): " add_account
        if [[ "$add_account" != "y" && "$add_account" != "Y" ]]; then break; fi
        read -p "  Enter a Nickname for this account: " cf_nickname
        read -p "  Enter the Cloudflare API Token for this account: " cf_token
        if [ -z "$cf_accounts_list" ]; then cf_accounts_list="$cf_nickname:$cf_token"; else cf_accounts_list="$cf_accounts_list,$cf_nickname:$cf_token"; fi
    done

    echo "TELEGRAM_BOT_TOKEN=$bot_token" > .env
    echo "TELEGRAM_ADMIN_IDS=$admin_ids" >> .env
    echo "CF_ACCOUNTS=$cf_accounts_list" >> .env
    echo -e "${GREEN}.env file created successfully.${NC}"

    if [ ! -f "config.json" ]; then
        echo '{"notifications":{"enabled":true,"chat_ids":[]},"failover_policies":[]}' > config.json
        echo -e "${GREEN}Initial config.json created.${NC}"
    fi
    
    echo -e "${GREEN}Building and starting the bot...${NC}"
    docker-compose up -d --build
    echo -e "${GREEN}Bot is running in the background!${NC}"
    echo -e "${YELLOW}Please start a chat with your bot and use the /settings command to configure everything else.${NC}"
    cd ..
}

main_menu() {
    while true; do
        print_header
        echo "1) Install or Reinstall Bot"
        echo "2) Update Bot from GitHub"
        echo "3) Edit Core Configuration (.env)"
        echo "4) View Live Logs"
        echo "5) Stop Bot"
        echo "6) Start Bot"
        echo "7) Remove Bot Completely"
        echo "8) Exit"
        read -p "Choose an option [1-8]: " choice

        case $choice in
            1)
                install_bot
                read -p "Press Enter to return to the main menu..."
                ;;
            2)
                echo -e "${YELLOW}Attempting to update the bot to the latest version from GitHub...${NC}"
                cd "$PROJECT_DIR" || { echo -e "${RED}Project directory not found.${NC}"; read -p "Press Enter..."; return; }
                git checkout main
                git fetch origin
                git reset --hard origin/main
                echo -e "${GREEN}Local repository has been successfully synced with GitHub.${NC}"
                docker-compose pull
                docker-compose up -d --build
                cd ..
                echo -e "${GREEN}Bot has been updated and restarted successfully!${NC}"
                read -p "Press Enter to return to the main menu..."
                ;;
            3)
                edit_config
                read -p "Press Enter to return to the main menu..."
                ;;
            4)
                cd "$PROJECT_DIR" && docker-compose logs -f && cd ..
                ;;
            5)
                cd "$PROJECT_DIR" && docker-compose stop && cd ..
                echo -e "${YELLOW}Bot stopped.${NC}"
                read -p "Press Enter to return to the main menu..."
                ;;
            6)
                cd "$PROJECT_DIR" && docker-compose start && cd ..
                echo -e "${GREEN}Bot started.${NC}"
                read -p "Press Enter to return to the main menu..."
                ;;
            7)
                read -p "Are you sure you want to remove the bot completely? This cannot be undone. (y/n): " confirm_remove
                if [[ "$confirm_remove" == "y" || "$confirm_remove" == "Y" ]]; then
                    cd "$PROJECT_DIR" && docker-compose down -v --rmi all && cd .. && rm -rf "$PROJECT_DIR"
                    echo -e "${RED}Bot completely removed.${NC}"
                else
                    echo -e "${YELLOW}Removal cancelled.${NC}"
                fi
                read -p "Press Enter to return to the main menu..."
                ;;
            8)
                exit 0
                ;;
            *)
                echo -e "${RED}Invalid option. Please try again.${NC}"
                read -p "Press Enter to continue..."
                ;;
        esac
        echo ""
    done
}

main_menu

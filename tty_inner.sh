export PATH='/c/Users/MCXS/工作流打通/ai衣橱/ai-wardrobe/scripts/bridge/shim':/c/Windows/system32:/c/Windows:/usr/bin
cd '/c/Users/MCXS/工作流打通/ai衣橱/ai-wardrobe/.local/tools/workbuddy-cli/node_modules/@tencent-ai/codebuddy-code'
'/c/Program Files/nodejs/node.exe' 'bin/codebuddy' --print --output-format stream-json --verbose --model deepseek-v4.1-flash --port 0 < 'tty_in.json' > 'tty_out.json' 2> 'tty_err.txt'
echo $? > 'tty_exit.txt'

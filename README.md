ts is written in python w gtk4 (wpydynamic is written in bash)

# important or sum
wpydynamic is not a standalone, its a bash script managing dynamic mode

dynamic basically makes the wallpaper change randomly in set intervals

its mandatory on path, the installer script does that automatically, thats why it asks for ur sudo password
tho always check what u are running to be sure ;)

wpydynamic will be placed on a default onpath folder, this case /usr/local/bin so just keep it that way OR move it from there to a diff onpath folder, without it, the program cant run

the installer is updated with every release so if u want to update waypaper-y, just run the installer again and enter "y" to ovveride older builds

the waypaper-y version grabbed by the installer is a compiled version of the python script, compiled via nuitka, if u dont trust it, find ur own way of using this

## easy installation
### Fish:
```
curl -fsSL https://raw.githubusercontent.com/rpd762/waypaper-y/refs/heads/main/install.sh > /tmp/wpy-install.sh && bash /tmp/wpy-install.sh && rm /tmp/wpy-install.sh
```
### Bash:
```
bash <(curl -fsSL https://raw.githubusercontent.com/rpd762/waypaper-y/refs/heads/main/install.sh)
```

from src: uhh idk, clone repo, compile if u wanna, make desktop entry, the install script does that automatically tho

# THIS WAS NOT TESTED MUCH WITH SWWW, OR OTHER WC's/DE's

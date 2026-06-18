# openhost-minecraft-server
Supports running Minecraft servers out of the box. 
- Version 1.12+
- Multiple servers at once
- Memory and CPU management

## how it works
Create new worlds at supported versions at any time. Each version JAR is downloaded from Minecraft on the first use, then for subsequent uses it's copied in. Similarly, Java runtime requirements are downloaded as necessary. 

Supports running arbitrary numbers of servers as long as there are ports. Each one can be assigned a certain amount of memory. 

## directory structure
- app-data/jre: the different JREs are stored here. Specific structure depends on OS. 
- app-data/versions: different Minecraft JARs are stored here.
- app-data/worlds: here, each world is stored in a different directory, containing standard Minecraft data, as well as a copy of the JAR for the corresponding version. On loadup, previous worlds (with JARs) are detected from here. 
Modifying these directories while the app is running, especially while servers are running, is not recommended. 

## port usage
The app is given five ports on the machine: 25565-25569. Multiple servers cannot simultaneously run on the same port. By default, it picks from 25565 then increments up. 
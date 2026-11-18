# SLOOP 3 (beta pública)

**SLOOP 3** es una reescritura de SLOOP hecha por isod89: cinco pistas (cuatro sintetizadores y la batería),
13 motores, 8 patrones por pista, una canción, samples y slices, y su propio editor web. Su autor lo llama una **beta
pública**: cuenta con errores, haz copias de seguridad y no dependas de él en el escenario todavía. No es SLOOP 2.x
con otro número: la app lo trata como un firmware propio.

## Qué hace la app con SLOOP 3

* **Lo reconoce.** Un FM-1 con SLOOP 3 aparece como «SLOOP 3 3.3.7 (beta pública)». Un FM-1 que espera en el
  MIDI MODE de m-bootloader aparece como «m-bootloader – MIDI MODE», con el chip flash que encontró.
* **Su editor.** La página [Editor](app://page/editor) muestra el editor web propio de SLOOP 3: el archivo del autor,
  sin cambios, incluido en la app, así que funciona sin internet. Desde esa página solo llegan al FM-1 los comandos
  del editor de SLOOP 3, notas y controladores.
* **Copias de seguridad.** [Copias](app://page/backups) › «Copiar ahora» guarda el proyecto (tal como está guardado
  y tal como está en ese momento), tus sonidos con favoritos y tema, tu banco FM6 y tus kits SYN en un archivo
  `sloop3-backup`. «Restaurar» los devuelve a un FM-1 con SLOOP 3. Detén antes la reproducción.
* **Una copia de toda la memoria flash**, mientras el FM-1 está en el MIDI MODE de m-bootloader:
  [Firmware+](app://page/firmware) › SLOOP 3 › «Copiar ahora toda la flash». La app solo lee y compara su copia con
  la suma de comprobación que calcula el propio chip flash.

## Qué no hace

* **Tus samples no están en un `sloop3-backup`.** SLOOP 3 no tiene ningún comando para entregarlos. Están en una
  copia de toda la flash o en los archivos desde los que los enviaste.
* **La app no instala SLOOP 3 ni instala m-bootloader.** Explica el camino y abre la página del instalador del autor
  en tu navegador.
* **Sin copia automática** para SLOOP 3: con una beta, la app solo lee cuando tú lo pides.
* El Studio no tiene gemelo de SLOOP 3. Las demás páginas del dispositivo (proyectos, presets, samples) hablan el
  protocolo de editor de Felucca, que SLOOP 3 no tiene: usa su editor.
* Grabar un sample con el micrófono no está disponible en el editor incluido; arrastra un archivo en su lugar.

## Antes de instalarlo

1. **Hace falta m-bootloader.** SLOOP 3 solo se instala mediante el MIDI MODE de m-bootloader. m-bootloader es un
   bootloader de código abierto de Julius Vitkauskas; instalarlo sustituye la zona de arranque del FM-1. Eso se hace
   una vez, fuera de esta app: con M-Flasher en modo de descarga USB o, con el firmware de M-VAVE, con M-UPGRADE de
   M-VAVE y `fm1-uboot.fwsc`.
2. **Copia primero toda la flash.** Es el único camino de vuelta: SLOOP 2.8 o el firmware de M-VAVE vuelven al
   escribir esa copia con M-Flasher. SLOOP 3 lee los proyectos, presets, samples y ajustes de SLOOP 2.8 una vez, en
   su primer arranque, y nunca los escribe.
3. **FM-1 más nuevos (vendidos desde finales de 2026, logo de M-VAVE en el frontal, chip flash UCUN UC25HQ80): no lo
   instales por ahora.** SLOOP 3 no se ha probado en esa revisión, m-bootloader está poco probado en ella y su autor
   informa de un FM-1 así que dejó de arrancar tras instalar m-bootloader. En MIDI MODE la app lee el chip y dice cuál
   es; para el más nuevo no ofrece la página del instalador. M-Flasher muestra lo mismo en FLASH CHIP: `0x856014` es
   el chip antiguo, `0xB36014` el nuevo.
4. Una actualización oficial de M-VAVE vuelve a poner el bootloader de M-VAVE. Después hay que instalar m-bootloader
   de nuevo.

## Conviene saber

* En MIDI MODE solo un programa puede hablar con el FM-1 a la vez: cierra la app (o desconecta y vuelve a conectar)
  antes de usar la página del instalador en el navegador.
* Si SLOOP 3 no arranca: mantén OCT− al encender para su RESCUE, o PLAY para el menú de m-bootloader.
* Todo esto se comprobó en el gemelo de PC del autor para SLOOP 3 3.3.6 y 3.3.7 (el código del firmware en un FM-1
  simulado) y contra el código del MIDI MODE de m-bootloader 2.4 en un PC: **nunca en un FM-1 real con esta app**.
* Más: [Cambiar de firmware](help:02-firmware) · [Copias de seguridad](help:04-backups) · [Enlaces](help:10-links)

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

## Su proyecto en la app

SLOOP 3 guarda **un proyecto** en el FM-1. La app lo lee igual que la copia de seguridad, solo lectura:

* [Vista de proyecto](app://page/project_view) › «Leer del FM-1» lo muestra con «música en curso» (tal como suena
  ahora, también los cambios sin guardar) o el proyecto 1 (el guardado en el FM-1): pistas, los 8 patrones por pista
  con sus notas, la canción, el tempo y el swing. Ahí puedes escucharlo y exportarlo como archivo MIDI (la batería en
  el canal 10), como CSV o como JSON. «Abrir archivo de proyecto …» también abre un `.s3p` del editor de SLOOP 3, una
  copia `.s3b` del editor o un `sloop3-backup`.
* [Proyectos](app://page/projects) lo trae a la biblioteca como archivo `.s3p`, el mismo archivo que guarda y abre la
  pestaña Projects del editor. La biblioteca muestra su tempo y su duración; «Añadir archivo …» admite archivos `.s3p`
  y `.s3b`, «Guardar como archivo …» deja una copia donde quieras.
* **Llevar un proyecto al FM-1 aún no está en la app.** Usa para ello la pestaña Projects del editor.
* No suenan en la escucha previa ni en la exportación MIDI: los fills, el arpegiador, la armonía y el strum, los
  bloqueos de parámetro (aparecen en el JSON) y los sonidos propios de los patrones. Un proyecto de un SLOOP 3 más
  nuevo que 3.4.0 no se abre: la app lo dice en vez de adivinar.

## Qué no hace

* **Tus samples no están en un `sloop3-backup`.** SLOOP 3 no tiene ningún comando para entregarlos. Están en una
  copia de toda la flash o en los archivos desde los que los enviaste.
* **La app instala SLOOP 3 solo a través del MIDI MODE de m-bootloader** – «Instalar SLOOP 3 con esta app (MIDI
  MODE)», sin conexión desde el SLOOP 3 3.4.0 que trae, tras una copia de toda la flash leída dos veces en MIDI MODE
  (aún no probado en un FM-1 real; ver la página de ayuda «Modo de actualización y rescate»). m-bootloader, que SLOOP 3
  necesita, también lo instala la app: como primer paso del mismo botón o en Recuperación › Herramientas › «Instalar
  m-bootloader». La página del instalador del autor sigue siendo el otro camino.
* **Sin copia automática** para SLOOP 3, salvo que la actives (ver abajo): con una beta, la app solo lee cuando tú lo
  pides.
* El Studio no tiene gemelo de SLOOP 3. Las demás páginas del dispositivo (presets, samples) hablan el
  protocolo de editor de Felucca, que SLOOP 3 no tiene: usa su editor.
* Grabar un sample con el micrófono no está disponible en el editor incluido; arrastra un archivo en su lugar.

## Estado, muestras de una copia de la flash, más para las copias

* **Valores en vivo.** [Estado del FM-1](app://page/fm1) muestra la carga de CPU de SLOOP 3 (media y pico), las voces
  que suenan, tempo y compás, y un nivel por pista, cada 2 s, leído con la misma petición de solo lectura que usa su
  editor. SLOOP 2.8 muestra igual la carga de CPU, el tempo y sus cuatro niveles. No se pregunta nada mientras una
  copia, una restauración o una instalación ocupa el FM-1.
* **Muestras de una copia de la flash.** [Copias de seguridad](app://page/backups) › “Copias de la flash” lista las
  copias de toda la flash (fecha, tamaño, chip y si el archivo aún coincide con la suma de control tomada al copiar).
  “Muestras a la biblioteca” saca USR1–USR4 como archivos WAV (“SLOOP 3 USR1 …”) a Muestras › “SLOOP 3 (flash)”;
  “Muestras de un archivo …” hace lo mismo con una copia guardada por M-Flasher. Sin conexión – no se envía nada.
  SLOOP 2.8 guarda sus muestras en los mismos cuatro lugares y con el mismo formato, así que una copia hecha con
  SLOOP 2.8 da sus muestras. «Volver a escribir …» vuelve a escribir una copia mediante M-Flasher, solo en el FM-1 del que viene (ver Modo de actualización y rescate).
* **El archivo de copia del propio editor (.s3b).** “Añadir archivo …” adopta un `.s3b` como copia de SLOOP 3 (el
  proyecto y tus sonidos – es todo lo que contiene un `.s3b`); “Como .s3b …” guarda una copia de SLOOP 3 para el
  “Restore” del editor.
* **Mirar dentro y comparar.** [Explorar y comparar](app://page/backup_browser) muestra de una copia de SLOOP 3 el
  proyecto (formato, tempo), tus 32 sonidos (S01–S32) con favoritos, las voces del banco FM6 (B01–B27), los kits SYN
  y los ajustes, y compara dos copias parte por parte y sonido por sonido. Solo se restaura entera (“Restaurar”).
* **La copia automática** para SLOOP 3 existe pero está **desactivada**: Copias de seguridad › “también para
  SLOOP 3”. Leer mientras SLOOP 3 suena aún no se ha probado en un FM-1 real.

## Antes de instalarlo

1. **Hace falta m-bootloader.** SLOOP 3 solo se instala mediante el MIDI MODE de m-bootloader. m-bootloader es un
   bootloader de código abierto de Julius Vitkauskas; instalarlo sustituye la zona de arranque del FM-1. Eso se hace
   una vez: con esta app (Recuperación › Herramientas, o el primer paso de «Instalar SLOOP 3», con M-Flasher en modo
   de descarga USB) o, con el firmware de M-VAVE, con M-UPGRADE de M-VAVE y `fm1-uboot.fwsc`.
2. **Copia primero toda la flash.** Es el único camino de vuelta: SLOOP 2.8 o el firmware de M-VAVE vuelven al
   escribir esa copia con M-Flasher. SLOOP 3 lee los proyectos, presets, samples y ajustes de SLOOP 2.8 una vez, en
   su primer arranque, y nunca los escribe.
3. **FM-1 más nuevos (vendidos desde finales de 2026, logo de M-VAVE en el frontal, chip flash UCUN UC25HQ80): solo
   m-bootloader 2.4 y SLOOP 3 3.4.0 o posteriores, bajo tu propio riesgo.** Su dueño informa de un FM-1 así en el que
   m-bootloader 2.4 y SLOOP 3 3.4.0 funcionan y guardar funciona (2026-11-19) – fuera de esta app, un solo equipo. Las
   versiones anteriores de m-bootloader son arriesgadas ahí: su autor informa de un FM-1 así que dejó de arrancar tras
   1.1 o 1.2. En MIDI MODE la app lee el chip y la versión de m-bootloader; para un equipo nuevo ofrece la página del
   instalador solo con m-bootloader 2.4 en la versión de la que se informó (en MIDI MODE
   «2.4-395e6cc») o 2.5 y posteriores (la copia de la flash, que solo lee, siempre en MIDI MODE). M-Flasher muestra el chip en FLASH CHIP:
   `0x856014` es el chip antiguo, `0xB36014` el nuevo.
4. Una actualización oficial de M-VAVE vuelve a poner el bootloader de M-VAVE. Después hay que instalar m-bootloader
   de nuevo.

## Conviene saber

* En MIDI MODE solo un programa puede hablar con el FM-1 a la vez: cierra la app (o desconecta y vuelve a conectar)
  antes de usar la página del instalador en el navegador.
* Si SLOOP 3 no arranca: mantén OCT− al encender para su RESCUE, o PLAY para el menú de m-bootloader.
* Todo esto se comprobó en el gemelo de PC del autor para SLOOP 3 3.3.6, 3.3.7 y 3.4.0 (el código del firmware en un FM-1
  simulado) y contra el código del MIDI MODE de m-bootloader 2.4 en un PC.
* **En un FM-1 real, una vez, solo lectura** (18-11-2026: un equipo anterior, una Raspberry Pi 5): la app reconoció
  SLOOP 3 3.4.0, su editor incluido se conectó, se leyó una copia de seguridad de SLOOP 3 (6 partes, todas las sumas de
  verificación válidas), se reconoció el MIDI MODE de m-bootloader 2.4 (sin confundirlo con un modo de actualización) y
  se rechazó una instalación con SLOOP 3 en marcha sin enviar nada. **No probado en un FM-1 real:** restaurar una copia
  de seguridad de SLOOP 3, la copia del flash en MIDI MODE, nada más que escriba y nada en un FM-1 más nuevo (logo delante) – lo que su dueño
  informa desde fuera de la app está arriba, en el punto 3. **Instalar SLOOP 3 con la app** (MIDI MODE, desde Melodee
  1.6.1) se hizo una vez en ese equipo anterior el 21-11-2026 – ver [Modo de actualización y recuperación](help:03-update-modus).
* Más: [Cambiar de firmware](help:02-firmware) · [Copias de seguridad](help:04-backups) · [Enlaces](help:10-links)

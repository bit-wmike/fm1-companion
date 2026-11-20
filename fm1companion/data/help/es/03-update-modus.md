# Modo de actualización y rescate

Si el FM-1 se queda a oscuras o no arranca bien tras una actualización interrumpida, el **modo de actualización** casi siempre
ayuda.

## Iniciar el modo de actualización

1. Apaga el FM-1.
2. **Mantén pulsada OCT−** (solo esta tecla) mientras lo enciendes.
3. **Tres destellos cortos** de la iluminación de las teclas significan: el FM-1 está en modo de actualización.
4. Conecta el USB. En la app, el dispositivo aparece con el añadido *(modo de actualización)*.
5. En [Firmware+ › Instalar y versiones](app://page/firmware) vuelve a instalar el firmware.

Melodee a partir de 1.3.5, con protección de arranque, vuelve por sí solo al modo de actualización tras una escritura interrumpida (también
un corte de corriente): basta con volver a encenderlo y reinstalar.

## Tras una instalación interrumpida

Tras una interrupción, el mensaje de la app dice lo que es *probable*: no ha vuelto a mirar el FM-1. La página
[Recuperación](app://page/recovery) muestra lo que se detecta en ese momento:

* **Se detecta un FM-1 en modo de actualización:** *Terminar instalación* vuelve a escribir el paquete
  interrumpido. La app lo conoce por su diario y comprueba que corresponde al cargador de actualización que espera;
  si no, no se envía nada.
* **No se detecta nada y el FM-1 funciona con normalidad:** no se escribió nada. *Olvidar la instalación
  interrumpida* elimina la nota de la app.
* **No se detecta nada y la pantalla sigue en negro:** apaga y enciende el FM-1 una vez y prueba el modo de
  actualización (arriba). Si no funciona, consulta la sección siguiente.

Con sinceridad: terminar una instalación interrumpida solo se ha ejecutado hasta ahora en la simulación, no en un
FM-1 real.

## Cuando no funciona nada

Si el ordenador muestra una unidad **“WL82 UBOOT1.00”** y la pantalla sigue negra, el chip está en su
modo de recuperación integrado. Entonces ayuda la página [Recuperación](app://page/recovery), **Etapa 2**: te guía
paso a paso por el MvaveFM1Unbricker incluido (con una doble copia de seguridad obligatoria de la flash).
Encontrarás información de fondo en la sección “Firmware update and recovery” de
[awesome-fm-1](https://github.com/cicloid/awesome-fm-1).

Lo que la Etapa 2 comprueba antes de escribir – en la copia de seguridad leída en la **misma** ejecución:

* **“Volver a escribir V15 original”** solo escribe bajo la cabecera de arranque de fábrica (V13–V15). Si la copia muestra la
  cabecera de arranque de V16 (el actualizador de M-VAVE la reescribe al instalar V16) o una cabecera desconocida
  (p. ej. m-bootloader), **no** se escribe **nada**: no está comprobado que V15 arranque bajo ella, y la app V16
  (0x04000–0xA8FFF) no se puede escribir con el unbricker integrado. En ese caso usa
  [M-Flasher](https://github.com/jvitkauskas/m-flasher) o `fm1_rescue.py` de
  [felucca-bsp](https://github.com/hugelton/felucca-bsp), que elige el paquete según la cabecera de arranque.
* **“Reparar ajustes”** restablece 0x93000–0xE8FFF y 0xFC000–0xFEFFF. Solo son datos puros con el
  **diseño de memoria de V15**. Con V16, 0x93000–0xA8FFF aún contiene firmware (final del programa, voces de fábrica): en ese caso,
  y con un diseño desconocido o dañado, no se escribe nada.
* Una sesión en modo de arranque ROM suele durar solo **una ejecución de la herramienta**. Después – también tras un rechazo – apaga y
  enciende el FM-1 y vuelve a ponerlo en modo de arranque ROM.

## Las herramientas incluidas: M-Flasher, m-bootloader, SLOOP 3

La app trae consigo **M-Flasher** 0.12.1 (para Raspberry Pi OS de 64 bits y PC x86-64; para el sistema de 32 bits
no existe), **m-bootloader** 2.4 y **SLOOP 3** 3.4.0, así que están disponibles sin internet. Recuperación ›
**Herramientas** muestra cada una: la versión incluida, la versión en uso, si su suma de verificación es correcta (la
app la comprueba antes de cada uso y nunca ejecuta un archivo modificado) y cuándo buscó por última vez una versión
nueva.

* **Versiones nuevas:** por defecto la app pregunta a GitHub una vez al día (Ajustes › General: a diario, cada
  semana, solo al iniciar o nunca; solo mientras la búsqueda de actualizaciones de firmware esté activada). Una
  versión nueva solo se muestra. **Actualizar** la descarga, la comprueba con la suma de verificación de GitHub y
  conserva tu versión actual como respaldo hasta que la nueva haya funcionado una vez. Un M-Flasher nuevo se usa
  solo para leer; uno con comandos cambiados se conserva pero no se usa.
* **Modo de descarga sin contraseña:** el instalador configura una regla que te permite acceder al FM-1 en modo de
  descarga ROM (USB 4c4a:8057) y carga el controlador de Linux `sg` al arrancar – `./install.sh` lo hace por defecto
  (`--no-download-mode-rule` lo omite; si no obtiene permisos de administrador, muestra los comandos exactos).
* **Copiar toda la flash** (Recuperación › Herramientas, o Copias de seguridad › Copias de la flash): la app pone el FM-1 en modo de
  descarga USB – sola, mientras funciona por USB MIDI (firmware original, familia Felucca, SLOOP 3, MIDI MODE de
  m-bootloader); si no, te dice cómo: con m-bootloader mantén **PLAY** al encenderlo y elige **DOWNLOAD MODE**, o
  mantén **REC** al encenderlo (va directamente). Luego M-Flasher lee toda la flash (1 MiB) **dos veces**; cada
  lectura se compara con el CRC-32 que calcula el propio chip, y solo si las dos lecturas son idénticas se guarda una
  copia en `Backups/<dispositivo>/Flash/`, con una descripción: la ID de unidad, el chip de flash, el SHA-256, la versión
  de M-Flasher, el firmware que la app conocía y la fecha. No se escribe nada en el FM-1. Si la app puso el FM-1 en
  modo de descarga, lo reinicia después. La ID de unidad y el chip que lee M-Flasher se guardan en el perfil del
  dispositivo (solo para la unidad que la app misma puso en modo de descarga, y solo si ningún otro perfil tiene ese
  ID).
* **Volver a escribir la flash** («Volver a escribir la flash …» en Recuperación › Herramientas, «Volver a escribir …»
  en Copias de seguridad › Copias de la flash): solo en el FM-1 del que viene la copia. La app lee primero la unidad conectada: **su
  ID de unidad y su chip de flash deben ser los de la copia** – una copia de otra unidad u otro chip se rechaza, sin
  excepción. Después copia el estado actual (leído dos veces, como arriba; sin esa copia no se escribe nada) y te
  pregunta otra vez, nombrando la unidad, su revisión, la fecha y el firmware de la copia: **vuelve toda la flash,
  incluida la zona de arranque (bootloader)**, y con ella el firmware, los proyectos, los sonidos y los ajustes de
  aquel día. Justo antes de escribir, M-Flasher muestra la ID de unidad, el chip y los sectores que va a escribir y espera
  un «yes»; la app solo responde si todo coincide con su propia comprobación; si no, M-Flasher no escribe nada.
  Después, la comprobación de M-Flasher (el CRC-32 del chip) y una lectura nueva deben ser iguales a la copia; luego
  se reinicia el FM-1. Una vez empezada la escritura no se puede cancelar – no desconectes el FM-1 entonces; aunque la app se cierre o falle, M-Flasher termina la escritura, y en el siguiente inicio la app dice qué pasó y pregunta qué hacer. Una copia con otros datos de fábrica (calibración, dirección Bluetooth) que la unidad se rechaza. Si algo
  sale mal, el FM-1 se queda en modo de descarga y la copia hecha justo antes vuelve por el mismo camino.
* **Al modo de descarga / Reiniciar el FM-1** (Recuperación › Herramientas y el menú Dispositivo): el mismo cambio
  por sí solo, sin leer nada – la app lo hace solo con un FM-1 que reconoció ella misma en su puerto USB; si no, te
  dice cómo. «Reiniciar el FM-1» vuelve a arrancar el firmware desde el modo de descarga. Ninguno escribe nada.
* **Instalar m-bootloader** (Recuperación › Herramientas): escribe el m-bootloader 2.4 que trae la app (con la suma
  de verificación comprobada) **solo en la zona de arranque 0x00000–0x03FFF**. Antes se copia toda la flash (leída dos
  veces, no se puede omitir) y la app te pregunta otra vez, nombrando la unidad y su revisión. En una unidad más nueva
  (logotipo delante) solo se ofrece una versión de m-bootloader que se sabe que funciona allí (2.4-395e6cc, según el
  informe de un propietario). Justo antes de escribir, la app responde a la pregunta de M-Flasher solo si cada sector
  previsto está dentro de la zona de arranque; después, una lectura nueva debe contener exactamente este bootloader y
  nada fuera de la zona de arranque puede haber cambiado. La app recuerda que este FM-1 tiene m-bootloader. Luego:
  mantén **PLAY** al encenderlo para ver su menú.
* **Quitar m-bootloader (zona de arranque original)**: vuelve a poner la zona de arranque del firmware original V15 de
  M-VAVE – la app descarga el paquete V15 una vez si no está en este ordenador y lo comprueba. La misma copia antes,
  las mismas comprobaciones; solo se escriben los sectores en los que tu zona de arranque difiere de la de V15. Esto
  también deshace lo que FM1 Open cambió en la zona de arranque. En un FM-1 que vino con V16 es la zona de arranque de
  V15, no necesariamente la que traía. SLOOP 3 se instala solo a través de m-bootloader: no se sabe si arranca después
  – la copia hecha justo antes es el camino de vuelta. **Solo en una unidad anterior** (sin logotipo delante): la zona
  de arranque de V15 nunca se ha escrito en una más nueva, así que allí la app lo rechaza y nombra el camino de vuelta
  – volver a escribir una copia de toda la flash de esta unidad hecha antes de m-bootloader (la ofrece si existe).
  «Reiniciar el FM-1» se rechaza mientras una escritura no terminó comprobada (el FM-1 se queda entonces en modo de
  descarga, donde se puede volver a escribir su copia).
* **Por qué importa la zona de arranque original:** muchos firmwares de la familia Felucca rechazan el V15 original si
  la zona de arranque no es la de V15 («FAILED -5» en el FM-1, CHECK HEAD). Si la app sabe que tu FM-1 tiene
  m-bootloader o la zona de arranque de FM1 Open (por una copia de la flash, por M-Flasher, por el propio m-bootloader
  o por el historial de firmware), un plan que caería en esa trampa (primero V15, o el propio V15 desde un firmware de
  la familia Felucca – y tras FM1 Open cualquier firmware de la familia Felucca) se detiene y te pide que antes vuelvas
  a poner la zona de arranque original. Una actualización del mismo firmware en un FM-1 con m-bootloader sigue
  funcionando; el plan avisa de que la vuelta posterior necesita la zona de arranque original.
* **Con sinceridad – lo que se probó en hardware real (20-11-2026, una Raspberry Pi 5, dos unidades):** la copia en
  ambas unidades (con el cambio de la propia app desde Melodee y desde SLOOP 3, con REC al encender, por el menú de
  m-bootloader), cada copia idéntica a una hecha a mano con M-Flasher; volver a escribir solo en una unidad anterior
  y solo cambios pequeños (un sector, verificado), el rechazo de la copia de otra unidad y el cierre de la app
  bloqueado mientras escribe. Aún no probado: volver a escribir en una unidad más nueva (logotipo delante), un cambio
  grande o la zona de arranque, un cierre de sesión real durante la escritura. Instalar y quitar m-bootloader y «Al
  modo de descarga» aún no se probaron con la app en un FM-1 real (el propietario hizo lo mismo a mano con
  M-Flasher). El firmware por el modo de descarga y SLOOP 3 a través de la app llegarán en versiones posteriores.

## Si el FM-1 no aparece como “WL82 UBOOT1.00”

El chip solo muestra la unidad cuando ha caído en el modo de arranque ROM. En este orden (según la
solución de problemas de MvaveFM1Unbricker), con el FM-1 conectado directamente al ordenador:

1. **Déjalo encendido dos minutos.** Los firmwares Felucca tienen un watchdog y una protección de arranque: tras dos
   arranques fallidos seguidos, el FM-1 pasa por sí solo al modo de arranque ROM. Luego vuelve a comprobarlo.
2. Mantén pulsadas **OCT− y OCT+** a la vez, enciende y sigue pulsando 10 segundos (modo de actualización, si el firmware llega
   hasta ahí; en Melodee es la calibración).
3. **Tres reinicios rápidos:** encender, esperar 10 s, apagar; encender, 10 s, apagar; encender. Si cada arranque falla, el
   tercero acaba en modo de arranque ROM. (Así es también como un FM-1 puede llegar ahí por accidente.)
4. Si nada de esto ayuda, solo queda el hardware: [Recuperación](app://page/recovery), **Etapa 3** (FM-1 Transporter).

## Causas típicas de interrupciones

* Cable USB flojo o demasiado largo, hub USB sin alimentación propia
* Subtensión en la Raspberry Pi – compruébalo con la [Comprobación del sistema](app://action/syscheck)
* Otro programa (editor en el navegador, DAW) se comunica con el FM-1 al mismo tiempo

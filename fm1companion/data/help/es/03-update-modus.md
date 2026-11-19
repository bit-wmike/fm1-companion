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
  descarga ROM (USB 4c4a:8057) y carga el controlador de Linux `sg` al arrancar.
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
  se reinicia el FM-1. Una vez empezada la escritura no se puede cancelar – no desconectes el FM-1 entonces. Si algo
  sale mal, el FM-1 se queda en modo de descarga y la copia hecha justo antes vuelve por el mismo camino.
* **Con sinceridad:** estos dos botones aún no se han usado con un FM-1 real a través de la app – solo con un
  sustituto de M-Flasher. M-Flasher mismo se usó exactamente así a mano (copiar dos veces, volver a escribir en la
  misma unidad, 18/19-11-2026). m-bootloader, el firmware por el modo de descarga y SLOOP 3 a través de la app
  llegarán en versiones posteriores.

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

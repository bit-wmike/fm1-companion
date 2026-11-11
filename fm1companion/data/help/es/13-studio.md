# Studio

El FM-1 es el instrumento para bocetar; la suite es el estudio. En la página [Studio](app://page/studio)
se ejecuta un **gemelo digital** de tu FM-1: el mismo firmware que en el dispositivo (Melodee, Skiff, JIANT FM, SLOOP,
CoralFlower o KARMA), con su estado completo (Melodee: los 32 proyectos; Skiff: los proyectos A–D y el banco de patrones;
JIANT FM: las secciones de cada canción y el índice de canciones; SLOOP: las secciones A–D, el banco FM6, los kits de
batería SYN y las ranuras de muestras; CoralFlower: los patrones y la canción del secuenciador con sus sonidos; KARMA: las
partes A–D de la canción, el orden de la canción, los kits de batería HEX y las ranuras de muestras; música en curso,
presets de usuario, ajustes). Suena idéntico – solo que por los altavoces del ordenador.

## Cómo funciona: FM-1 → Studio → WAV o de vuelta

1. **Traer del FM-1:** lee una copia de seguridad completa del FM-1 activo (su nombre aparece en el botón) y la carga en
   el gemelo. La copia también se guarda en [Copias de seguridad](app://page/backups) – no se pierde nada. No se cambia nada
   en el FM-1 (la reproducción se detiene brevemente).
   Como alternativa, **Abrir desde copia de seguridad …** (cualquier copia de Melodee, Skiff, JIANT FM, SLOOP, CoralFlower o KARMA; también copias de SLOOP y KARMA en el formato
   del editor web de SLOOP – arranca el gemelo del firmware
   de la copia) o
   **Abrir el último estado del Studio**.
2. **Tocar y pulir:** el FM-1 de la pantalla responde al ratón, al teclado del ordenador (fila inferior
   Z S X D C …, fila superior Q 2 W 3 E …), a un teclado MIDI (opción “Entrada MIDI”) y, si quieres, a las teclas del
   FM-1 real. Controles: arrastrar, rueda del ratón o teclas de flecha. **Espacio** inicia/detiene. Cargas los proyectos (Melodee 1–32,
   Skiff A–D, JIANT FM: las secciones A–D de la canción actual, SLOOP: sus secciones A–D, KARMA: las partes A–D de la
   canción) desde la lista de la derecha.
3. **Exportar como WAV …:** toda la canción hasta el final, un número fijo de segundos o de pasadas del patrón.
   Por defecto WAV de 24 bits (44,1 kHz estéreo), opcionalmente coma flotante de 32 bits, guardado en la carpeta
   “FM-1 Studio” de tu carpeta de música, con el nombre del proyecto y la fecha en el nombre del archivo. **Pistas sueltas (pistas separadas):** un
   archivo aparte por pista, tan largo como la mezcla – para seguir trabajando en Logic o Audacity. Cada pista se renderiza
   por separado, con su parte de los efectos compartidos (reverb, delay, master); por eso la suma de las pistas separadas
   no es idéntica bit a bit a la mezcla.
4. **Volver a escribir en el FM-1 …:** pregunta primero, nombra el dispositivo, hace automáticamente una
   **copia de seguridad previa** y luego escribe proyectos, música en curso y presets. Los ajustes del dispositivo
   se quedan como están, salvo que marques la casilla. Solo funciona con acceso completo (Melodee, Skiff, JIANT FM, SLOOP, CoralFlower
   o KARMA conectado – el mismo firmware que el del gemelo) y nunca en el propio gemelo. SLOOP solo escribe con su
   canción detenida (STOP).

## Estado del Studio y deshacer

El gemelo recuerda todo lo que un FM-1 conserva tras apagarse (la flash), y la app lo guarda
automáticamente – por dispositivo, en cuanto algo cambia y al salir de la página. **Recordar estado intermedio**
añade un punto a la lista; **Deshacer** (Ctrl+Z) y **Rehacer** (Ctrl+Shift+Z) saltan
entre los estados (hasta 30). Como en el dispositivo: lo que aún no se ha guardado en el proyecto en curso (SAVE)
no forma parte del estado.

## Versiones

El gemelo existe en varias versiones (Melodee 1.6.0-rc.7 … 1.6.1, Skiff 2.1.1 y 2.2, JIANT FM V10.6z2 – la versión estable
del instalador de JIANT – además de V10.60 y V10.61, SLOOP 2.8.5 – la versión del instalador de SLOOP – y 2.8.4,
CoralFlower 0.7.1 – la de su instalador – y 0.7, KARMA 3.1 – la versión que ofrece su instalador – además de 3.0.5). La app
elige el firmware
y la versión de tu FM-1 o de la copia de seguridad. Si la versión no coincide exactamente, un aviso arriba dice: “El Studio
funciona con Melodee …, tu FM-1 tiene … – el sonido puede diferir.”
Volver a escribir en un FM-1 con otra versión solo es posible entonces marcando la casilla de experto.

## El gemelo como dispositivo

**Usar como dispositivo** (o “Gemelo del Studio … virtual” en la selección de dispositivo de arriba) convierte el gemelo en el
dispositivo activo: [Proyectos](app://page/projects), [Componer](app://page/compose), Presets, Copias de seguridad y el Editor
trabajan entonces con él. Siempre aparece marcado como “virtual”; instalar firmware, el rescate, el audio USB y las
copias de seguridad automáticas no están disponibles para él. Para volver: elige tu FM-1 arriba.

Al reproducir en Proyectos y Componer, el destino sin FM-1 se llama **“en el Studio (sonido idéntico)”**. La
antigua aproximación en el ordenador solo se mantiene cuando no hay gemelo para el firmware.

## Límites

* Por ahora **Melodee**, **Skiff**, **JIANT FM**, **SLOOP**, **CoralFlower** y **KARMA**. El firmware original (mediante
  FM1 Open) llegará más adelante.
* Skiff: toda la canción hasta el final solo termina con SONG ONCE (si no, Skiff repite en bucle); el gemelo de Skiff tiene un núcleo
  (MENU › CPU 2 CORES no tiene efecto), sin audio USB ni MIDI TRS. La vista de proyecto aún no lee las canciones de Skiff.
* JIANT FM: las pistas separadas 5 y 6 se quedan en silencio mientras MENU › TRACKS sea 4 (el valor por defecto de JIANT: T5 y T6 dormidas). El
  gemelo tiene un núcleo, sin Bluetooth (PROX no recibe señal), sin host USB y sin audio USB (el motor INPUT oye silencio).
  Una versión de JIANT sin gemelo propio (p. ej. V10.6c … V10.6z10) funciona con el gemelo anterior más cercano, con un aviso. A partir de
  V10.6p JIANT tiene 6 canciones: las canciones 7 y 8 de una copia más antigua se omiten (el Studio las nombra).
  La vista de proyecto aún no lee las canciones de JIANT.
* SLOOP: “Toda la canción hasta el final” reproduce el orden de canción de SLOOP (capa SAVE › SONG MODE; el orden forma parte
  de los ajustes) – si nombra una sección vacía, se reproduce el bucle (hasta el límite de la exportación, como con una
  canción en LOOP). El gemelo tiene un núcleo (MENU › 2ND CORE no tiene efecto), sin audio USB, sin MIDI TRS y sin rescate
  USB. Un SLOOP más antiguo (p. ej. 2.8.3) funciona con el gemelo 2.8.4, con un aviso. La vista de proyecto aún no lee las
  secciones de SLOOP (el editor web de SLOOP las muestra).
* CoralFlower: sin ranuras de proyecto – sus ocho patrones y la canción se reproducen desde el panel del gemelo (SEQ, PLAY)
  o con Play; la música se hace en el panel (CoralFlower no tiene editor). Las cuatro pistas separadas son sus partes:
  acordes, bajo, capa, batería (una pista separada no lleva ni el clic del metrónomo ni el sonido de encendido; la mezcla
  tiene ambos, como los reproduce el dispositivo). Después de cargar una copia y después de volver a escribir, CoralFlower se
  reinicia (tanto el gemelo como el FM-1): así lee lo restaurado. El gemelo tiene un núcleo, sin audio USB ni MIDI TRS; el
  gesto de actualización (OCT− + OCT+ + HOME mantenidos) no hace nada allí. La vista de proyecto no lee los patrones de
  CoralFlower.
* KARMA: el gemelo tiene un núcleo (como la compilación de un solo núcleo del propio KARMA), sin audio USB (MENU › USB AUDIO
  = STEMS no tiene efecto), sin MIDI TRS y sin rescate USB. KARMA guarda sus copias en el formato de SLOOP; una copia de
  SLOOP (hasta 2.6) se puede escribir en un FM-1 con KARMA (los kits SYN de SLOOP 2.5 se omiten, como hace el editor de
  KARMA), una copia de KARMA nunca en SLOOP. Las tres canciones guardadas de KARMA (SAVE en la pantalla SONG) no forman
  parte de una copia – la copia propia de KARMA tampoco las incluye. La vista de proyecto aún no lee las partes de KARMA.
* ChoralRoot FM-1 (gemelos de 0.39 – la versión que ofrece su instalador – y 0.38; un FM-1 que responde
  “ChoralRoot …” o una copia del instalador de ChoralRoot lo abre): sin ranuras de proyecto – sus diez ranuras de loop
  se eligen en el panel del gemelo (PLAY mantenido, PRESETS), Play reproduce el loop; la música se hace en el panel
  (ChoralRoot no tiene editor), y solo SAVE mantenido escribe los loops y los ajustes, como en el dispositivo. Las dos
  pistas separadas son sus partes de acordes y de bajo (una pista separada no lleva el clic del metrónomo). Después de
  cargar una copia y después de volver a escribir, ChoralRoot se reinicia (el gemelo y también el FM-1). Las copias de
  0.21 y anteriores guardan los sonidos de usuario en objetos que el firmware nuevo ya no tiene: el Studio los omite y
  los nombra. El gemelo tiene un núcleo, sin audio USB, sin MIDI TRS y sin Bluetooth; el gesto de actualización no hace
  nada allí.
* El gemelo no tiene límite de CPU: el dispositivo descarta voces cuando se sobrecarga, el gemelo nunca. Las partes en coma flotante
  pueden diferir en los últimos bits (inaudible). Sin Bluetooth, sin host USB, sin actualización de firmware en el
  gemelo.
* El sonido sale por la salida predeterminada del sistema. Si se elige una salida concreta en “Salida”,
  la app mueve allí el sonido del Studio (necesita `pactl`, paquete pulseaudio-utils).
* El reloj del gemelo avanza mientras la página está visible o algo está sonando; si no, descansa (ahorra tiempo de cálculo).

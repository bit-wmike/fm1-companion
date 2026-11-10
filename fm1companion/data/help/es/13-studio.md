# Studio

El FM-1 es el instrumento para bocetar; la suite es el estudio. En la página [Studio](app://page/studio)
se ejecuta un **gemelo digital** de tu FM-1: el mismo firmware que en el dispositivo (Melodee, Skiff o JIANT FM), con su estado
completo (Melodee: los 32 proyectos; Skiff: los proyectos A–D y el banco de patrones; JIANT FM: las secciones de cada canción y el índice
de canciones; música en curso, presets de usuario, ajustes). Suena idéntico – solo que por los altavoces del ordenador.

## Cómo funciona: FM-1 → Studio → WAV o de vuelta

1. **Traer del FM-1:** lee una copia de seguridad completa del FM-1 activo (su nombre aparece en el botón) y la carga en
   el gemelo. La copia también se guarda en [Copias de seguridad](app://page/backups) – no se pierde nada. No se cambia nada
   en el FM-1 (la reproducción se detiene brevemente).
   Como alternativa, **Abrir desde copia de seguridad …** (cualquier copia de Melodee, Skiff o JIANT FM – arranca el gemelo del firmware
   de la copia) o
   **Abrir el último estado del Studio**.
2. **Tocar y pulir:** el FM-1 de la pantalla responde al ratón, al teclado del ordenador (fila inferior
   Z S X D C …, fila superior Q 2 W 3 E …), a un teclado MIDI (opción “Entrada MIDI”) y, si quieres, a las teclas del
   FM-1 real. Controles: arrastrar, rueda del ratón o teclas de flecha. **Espacio** inicia/detiene. Cargas los proyectos (Melodee 1–32,
   Skiff A–D, JIANT FM: las secciones A–D de la canción actual) desde la lista de la derecha.
3. **Exportar como WAV …:** toda la canción hasta el final, un número fijo de segundos o de pasadas del patrón.
   Por defecto WAV de 24 bits (44,1 kHz estéreo), opcionalmente coma flotante de 32 bits, guardado en la carpeta
   “FM-1 Studio” de tu carpeta de música, con el nombre del proyecto y la fecha en el nombre del archivo. **Pistas sueltas (pistas separadas):** un
   archivo aparte por pista, tan largo como la mezcla – para seguir trabajando en Logic o Audacity. Cada pista se renderiza
   por separado, con su parte de los efectos compartidos (reverb, delay, master); por eso la suma de las pistas separadas
   no es idéntica bit a bit a la mezcla.
4. **Volver a escribir en el FM-1 …:** pregunta primero, nombra el dispositivo, hace automáticamente una
   **copia de seguridad previa** y luego escribe proyectos, música en curso y presets. Los ajustes del dispositivo
   se quedan como están, salvo que marques la casilla. Solo funciona con acceso completo (Melodee, Skiff o JIANT FM conectado – el
   mismo firmware que el del gemelo) y nunca en el propio gemelo.

## Estado del Studio y deshacer

El gemelo recuerda todo lo que un FM-1 conserva tras apagarse (la flash), y la app lo guarda
automáticamente – por dispositivo, en cuanto algo cambia y al salir de la página. **Recordar estado intermedio**
añade un punto a la lista; **Deshacer** (Ctrl+Z) y **Rehacer** (Ctrl+Shift+Z) saltan
entre los estados (hasta 30). Como en el dispositivo: lo que aún no se ha guardado en el proyecto en curso (SAVE)
no forma parte del estado.

## Versiones

El gemelo existe en varias versiones (Melodee 1.6.0-rc.7 … 1.6.1, Skiff 2.1.1 y 2.2, JIANT FM V10.6z2 – la versión estable
del instalador de JIANT – además de V10.60 y V10.61). La app elige el firmware
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

* Por ahora **Melodee**, **Skiff** y **JIANT FM**. SLOOP y el firmware original (mediante FM1 Open) llegarán más adelante.
* Skiff: toda la canción hasta el final solo termina con SONG ONCE (si no, Skiff repite en bucle); el gemelo de Skiff tiene un núcleo
  (MENU › CPU 2 CORES no tiene efecto), sin audio USB ni MIDI TRS. La vista de proyecto aún no lee las canciones de Skiff.
* JIANT FM: las pistas separadas 5 y 6 se quedan en silencio mientras MENU › TRACKS sea 4 (el valor por defecto de JIANT: T5 y T6 dormidas). El
  gemelo tiene un núcleo, sin Bluetooth (PROX no recibe señal), sin host USB y sin audio USB (el motor INPUT oye silencio).
  Una versión de JIANT sin gemelo propio (p. ej. V10.6c … V10.6z10) funciona con el gemelo anterior más cercano, con un aviso. A partir de
  V10.6p JIANT tiene 6 canciones: las canciones 7 y 8 de una copia más antigua se omiten (el Studio las nombra).
  La vista de proyecto aún no lee las canciones de JIANT.
* El gemelo no tiene límite de CPU: el dispositivo descarta voces cuando se sobrecarga, el gemelo nunca. Las partes en coma flotante
  pueden diferir en los últimos bits (inaudible). Sin Bluetooth, sin host USB, sin actualización de firmware en el
  gemelo.
* El sonido sale por la salida predeterminada del sistema. Si se elige una salida concreta en “Salida”,
  la app mueve allí el sonido del Studio (necesita `pactl`, paquete pulseaudio-utils).
* El reloj del gemelo avanza mientras la página está visible o algo está sonando; si no, descansa (ahorra tiempo de cálculo).

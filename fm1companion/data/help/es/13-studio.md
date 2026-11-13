# Studio

El FM-1 es el instrumento para bocetar; la suite es el estudio. En la página [Studio](app://page/studio)
se ejecuta un **gemelo digital** de tu FM-1: el mismo firmware que en el dispositivo (Melodee, Skiff, JIANT FM, SLOOP,
CoralFlower, KARMA, Felucca, ChoralRoot FM-1, X0X, FoMni, AMB-1, LOOM-8 o FM1 Open de M-VAVE), con su estado completo (Melodee: los 32 proyectos; Skiff: los proyectos A–D y el banco de patrones;
JIANT FM: las secciones de cada canción y el índice de canciones; SLOOP: las secciones A–D, el banco FM6, los kits de
batería SYN y las ranuras de muestras; CoralFlower: los patrones y la canción del secuenciador con sus sonidos; KARMA: las
partes A–D de la canción, el orden de la canción, los kits de batería HEX y las ranuras de muestras; FM1 Open: sus 16
proyectos, la biblioteca DX7 y el banco de muestras; música en curso, presets de usuario, ajustes). Suena idéntico – solo
que por los altavoces del ordenador. (La excepción es FM1 Open: consulta más abajo “El firmware original y FM1 Open”.)
También **Felucca**, la base de la familia, tiene su gemelo (los proyectos A–D, los presets de usuario con sus patches
FM6 y las ranuras de muestras USR1–USR3); abre copias de seguridad de Felucca (también las del propio editor web de
Felucca), lee de un FM-1 con Felucca y vuelve a escribir en él como los demás. ChoralRoot FM-1, X0X, FoMni, AMB-1 y
LOOM-8 también tienen gemelos; lo que cambia en ellos (ranuras de loop, una copia solo completa, sin copia por USB) está
más abajo, en “Límites”.

## Cómo funciona: FM-1 → Studio → WAV o de vuelta

1. **Traer del FM-1:** lee una copia de seguridad completa del FM-1 activo (su nombre aparece en el botón) y la carga en
   el gemelo. La copia también se guarda en [Copias de seguridad](app://page/backups) – no se pierde nada. No se cambia nada
   en el FM-1 (la reproducción se detiene brevemente). Una excepción: FM1 Open no puede entregar directamente un proyecto
   guardado – el FM-1 carga cada proyecto guardado, uno tras otro, en su área de trabajo, y al final la app vuelve a poner
   tu proyecto de trabajo. Si se desconecta el cable entre medias, puede perderse el trabajo sin guardar del área de trabajo.
   Como alternativa, **Abrir desde copia de seguridad …** (cualquier copia de Melodee, Skiff, JIANT FM, SLOOP, CoralFlower, KARMA, Felucca, ChoralRoot, X0X, LOOM-8 o FM1 Open; también copias de SLOOP y KARMA en el formato
   del editor web de SLOOP – arranca el gemelo del firmware
   de la copia) o
   **Abrir el último estado del Studio**.
2. **Tocar y pulir:** el FM-1 de la pantalla responde al ratón, al teclado del ordenador (fila inferior
   Z S X D C …, fila superior Q 2 W 3 E …), a un teclado MIDI (opción “Entrada MIDI”) y, si quieres, a las teclas del
   FM-1 real. Controles: arrastrar, rueda del ratón o teclas de flecha. **Espacio** inicia/detiene. Cargas los proyectos (Melodee 1–32,
   Skiff A–D, JIANT FM: las secciones A–D de la canción actual, SLOOP: sus secciones A–D, KARMA: las partes A–D de la
   canción, FM1 Open 1–16) desde la lista de la derecha.
3. **Exportar como WAV …** (en la fila superior de botones): un número fijo de segundos, pasadas del patrón o toda la
   canción hasta el final. “Toda la canción” solo es la opción por defecto con Melodee, cuya cadena de canción se detiene
   al final. Skiff, JIANT FM, SLOOP, KARMA, Felucca, CoralFlower y X0X solo se detienen si su canción está ajustada para
   sonar una vez – una canción en bucle sigue hasta el límite de 15 minutos, y la app lo dice después. En los gemelos sin
   canción (FM1 Open, ChoralRoot, LOOM-8, FoMni, AMB-1) la opción está desactivada. **Cancelar** detiene una exportación
   en curso y no deja ningún archivo.
   Por defecto WAV de 24 bits (44,1 kHz estéreo), opcionalmente coma flotante de 32 bits, guardado en la carpeta
   “FM-1 Studio” de tu carpeta de música, con el nombre del proyecto y la fecha en el nombre del archivo; nunca se
   sobrescribe un archivo existente (el nuevo recibe un número). **Pistas sueltas (pistas separadas):** un
   archivo aparte por pista (`…_track1.wav` …), tan largo como la mezcla – para seguir trabajando en Logic o Audacity. La
   mezcla y todas las pistas parten de la misma instantánea del gemelo, así que coinciden en el tiempo al apilarlas. Cada
   pista se renderiza por separado, con su parte de los efectos compartidos (reverb, delay, master); por eso la suma de
   las pistas separadas no es la mezcla – los niveles y la dinámica difieren.
4. **Volver a escribir en el FM-1 …:** pregunta primero, nombra el dispositivo, hace automáticamente una
   **copia de seguridad previa** y luego escribe proyectos, música en curso y presets. El diálogo dice de dónde viene el
   contenido del gemelo. Si se trajo de este mismo FM-1, solo se escriben las partes que cambiaste en el Studio; el resto
   se queda como está en el dispositivo. Si el gemelo se inició vacío o viene de una copia de seguridad, de otro
   dispositivo o de un estado anterior, un aviso en rojo lo dice y hace falta una segunda casilla – entonces todo lo
   enumerado se sustituye en el FM-1. Los ajustes del dispositivo
   se quedan como están, salvo que marques la casilla. Solo funciona con acceso completo (Melodee, Skiff, JIANT FM, SLOOP, CoralFlower,
   KARMA o FM1 Open conectado – el mismo firmware que el del gemelo) y nunca en el propio gemelo. SLOOP y KARMA solo escriben con su
   canción detenida (STOP). JIANT FM: un gemelo y un FM-1 a distinto lado del cambio de JIANT de 8 a 6 canciones
   (V10.6p) no se pueden escribir el uno en el otro.

## Estado del Studio y deshacer

El gemelo recuerda todo lo que un FM-1 conserva tras apagarse (la flash), y la app lo guarda
automáticamente – por dispositivo, en cuanto algo cambia y al salir de la página. **Recordar estado intermedio**
añade un punto a la lista; **Deshacer** (Ctrl+Z) y **Rehacer** (Ctrl+Shift+Z) saltan
entre los estados (hasta 30). Como en el dispositivo: lo que aún no se ha guardado en el proyecto en curso (SAVE)
no forma parte del estado. Deshacer también recupera de dónde venía el estado (la línea bajo los botones).

Si alguna vez el gemelo se detiene solo (su proceso terminó, normalmente porque el ordenador se quedó sin memoria), la app lo dice y ofrece volver a abrir el estado guardado automáticamente. Mientras la ventana está minimizada o se muestra otra página, el gemelo se pausa para ahorrar procesador – salvo que esté sonando o exportando. Si el sonido se corta en un ordenador ocupado, el Studio lo cuenta en “Sonido y MIDI” y amplía por sí mismo su búfer de sonido (algo más de retardo, menos chasquidos).

## Versiones

El gemelo existe en varias versiones (Melodee 1.6.0-rc.7 … 1.6.1, Skiff 2.1.1 y 2.2, JIANT FM V10.6z2 – la versión estable
del instalador de JIANT – además de V10.60 y V10.61, SLOOP 2.8.5 – la versión del instalador de SLOOP – y 2.8.4,
CoralFlower 0.7.1 – la de su instalador – y 0.7, KARMA 3.1 – la versión que ofrece su instalador – además de 3.0.5, FM1 Open V14). La app
elige el firmware
y la versión de tu FM-1 o de la copia de seguridad. Si la versión no coincide exactamente, un aviso arriba dice: “El Studio
funciona con Melodee …, tu FM-1 tiene … – el sonido puede diferir.”
Volver a escribir en un FM-1 con otra versión solo es posible entonces marcando la casilla de experto.
Felucca: 1.8.1 – la versión del instalador de Felucca – y 1.8.0 (una beta 1.8.2 funciona con el gemelo 1.8.1, con el
aviso).

## El firmware original y FM1 Open

La mayoría de los FM-1 usan el **firmware original V15** de M-VAVE (o el más reciente V16). Su código no es abierto, así
que no hay gemelo de él. M-VAVE ha publicado un firmware abierto, **FM1 Open** (V14, github.com/m-vave/fm1_open): un
firmware nuevo y propio – 8 pistas, 16 proyectos, 8 pads de muestras, su propio manejo – que conserva las **128 voces DX7
de fábrica de la V15** en su orden (tomadas del paquete V15) y el protocolo de actualización. Su motor de sonido en el
dispositivo es un binario cerrado; el simulador para navegador de M-VAVE usa en su lugar un motor abierto, y el gemelo
FM1 Open del Studio es exactamente ese código del simulador.

* **FM-1 con FM1 Open:** el gemelo es su propio firmware (V14). Traer, volver a escribir y las copias de seguridad (en
  su propio formato: el proyecto de trabajo, los 16 proyectos, los cuatro bancos de la biblioteca DX7, el banco de
  muestras) funcionan con el protocolo de archivos propio de FM1 Open. Para leer un proyecto, el FM-1 lo carga un
  momento; después vuelve a mostrar el proyecto de antes, marcado como modificado. Los proyectos vacíos en la copia se
  quedan en el FM-1 como están (FM1 Open no puede borrar una ranura por USB). El sonido sigue al simulador de M-VAVE y
  puede diferir un poco del dispositivo.
* **FM-1 con la V15/V16 original:** el Studio arranca en su lugar el gemelo FM1 Open, con un aviso: las mismas voces de
  fábrica, pero otro firmware – el manejo, los proyectos, los efectos y el sonido pueden diferir (la V16 además corrigió
  el detune). El firmware original **no tiene protocolo de copias de seguridad**: no se puede traer nada de él ni volver
  a escribirlo (la única copia es un volcado completo en [Rescate](app://page/recovery)). Lo que la app sí puede hacer
  con la V15 es la restauración de voces de M-VAVE: en [Voces FM](app://page/voices), **V15 original:
  restaurar voces de fábrica …** sobrescribe las 128 voces de los bancos A–D con las voces de fábrica (del paquete V15
  de M-VAVE, fijado por suma de comprobación). Las voces que había antes en el FM-1 **no se pueden guardar primero** –
  las voces que guardaste tú se pierden. La app no ofrece escribir voces propias en un banco de la V15 (antes haría falta
  una prueba en el dispositivo).

## El gemelo como dispositivo

**Usar como dispositivo** (o “Gemelo del Studio … virtual” en la selección de dispositivo de arriba) convierte el gemelo en el
dispositivo activo: [Proyectos](app://page/projects), [Componer](app://page/compose), Presets, Copias de seguridad y el Editor
trabajan entonces con él. Siempre aparece marcado como “virtual”; instalar firmware, el rescate, el audio USB y las
copias de seguridad automáticas no están disponibles para él. Para volver: elige tu FM-1 arriba.

Al reproducir en Proyectos y Componer, el destino sin FM-1 se llama **“en el Studio (sonido idéntico)”**. La
antigua aproximación en el ordenador solo se mantiene cuando no hay gemelo para el firmware.

## Límites

* Por ahora **Melodee**, **Skiff**, **JIANT FM**, **SLOOP**, **CoralFlower**, **KARMA** y **FM1 Open**, que también
  sustituye al firmware original V15/V16 (ver arriba).
* FM1 Open: el gemelo no tiene Bluetooth, audio USB, MIDI TRS ni actualizador; su motor abierto sustituye al del
  dispositivo. La vista de proyecto no lee los proyectos de FM1 Open.
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
* Felucca: el gemelo tiene un núcleo (MENU › SYSTEM › 2ND CORE no tiene efecto), sin presión de teclas para
  AFTERTOUCH (las teclas están pulsadas o no), sin audio USB y sin MIDI TRS. Un **fork de Felucca** que solo se
  identifica como «FELUCCA v…» (o que elegiste para el dispositivo, p. ej. Felucca MPE) recibe el gemelo de Felucca –
  con un aviso arriba de que puede diferir; lo mismo para una compilación de Felucca con otra identidad de paquete (una
  compilación de desarrollo: FM-1_900). Vuelve a escribir solo si sabes que el fork lee los datos de Felucca – el FM-1
  comprueba cada parte y antes se hace una copia de seguridad. La vista de proyecto aún no lee los proyectos de
  Felucca.
* ChoralRoot FM-1 (gemelos de 0.39 – la versión que ofrece su instalador – y 0.38; un FM-1 que responde
  “ChoralRoot …” o una copia del instalador de ChoralRoot lo abre): sin ranuras de proyecto – sus diez ranuras de loop
  se eligen en el panel del gemelo (PLAY mantenido, PRESETS), Play reproduce el loop; la música se hace en el panel
  (ChoralRoot no tiene editor), y solo SAVE mantenido escribe los loops y los ajustes, como en el dispositivo. Las dos
  pistas separadas son sus partes de acordes y de bajo (una pista separada no lleva el clic del metrónomo). Después de
  cargar una copia y después de volver a escribir, ChoralRoot se reinicia (el gemelo y también el FM-1). Las copias de
  0.21 y anteriores guardan los sonidos de usuario en objetos que el firmware nuevo ya no tiene: el Studio los omite y
  los nombra. El gemelo tiene un núcleo, sin audio USB, sin MIDI TRS y sin Bluetooth; el gesto de actualización no hace
  nada allí.
* X0X (1.2.1, su versión más reciente, y 1.2.0): un proyecto – su sonido, los 32 patrones, la canción y el movimiento
  de los mandos –, sin ranuras de proyecto: los patrones y la canción suenan desde el panel del gemelo o con Reproducir.
  Las cinco pistas por separado son sus partes: la 909, la 808, 303 A, 303 B y BREAK. X0X tiene un formato de copia
  propio: **Abrir desde copia de seguridad …** abre también los archivos `.x0b` del propio X0X, y X0X solo acepta una
  copia entera. Al escribir de vuelta, X0X debe estar detenido y pregunta en su pantalla (RESTORE FROM USB?): pulsa
  **SEL** en el FM-1. Los loops de break de las ranuras de samples no forman parte de una copia de X0X. El gemelo tiene
  un núcleo, sin audio USB, sin MIDI TRS y sin calibración del hardware; el gesto de actualización no hace nada allí.
  La vista de proyecto no lee los patrones de X0X.
* FoMni (0.4.1 y 0.4) y AMB-1 (1.3.2 y 1.3.1) no tienen copia de seguridad por USB: sus gemelos arrancan vacíos (o
  desde un estado del estudio) y nunca se cargan desde un FM-1 ni se escriben en uno – tócalos, expórtalos como WAV y
  guarda sus estados en el estudio. Las cuatro pistas por separado de FoMni son sus voces de arpa 1 y 2, el acorde con
  el bajo y el ritmo; AMB-1 suena solo desde el principio, sus cuatro pistas son sus capas: pad, campanas, bajo y pulso.
* LOOM-8 (0.14 y 0.13.1): su copia es la de fm1-base (los presets de usuario y los tres proyectos, solo enteros;
  **Abrir desde copia de seguridad …** abre también los archivos `.loom8` del propio LOOM-8). Al escribir de vuelta,
  LOOM-8 debe estar detenido y pregunta en su pantalla: pulsa **SEL** en el FM-1. Su banco de samples no forma parte
  de la copia. Las ocho pistas por separado son sus pistas. El gemelo tiene un núcleo, sin Bluetooth, sin host USB y
  sin audio USB; la vista de proyecto no lee los proyectos de LOOM-8.
* El gemelo no tiene límite de CPU: el dispositivo descarta voces cuando se sobrecarga, el gemelo nunca. Las partes en coma flotante
  pueden diferir en los últimos bits (inaudible). Sin Bluetooth, sin host USB, sin actualización de firmware en el
  gemelo.
* El sonido sale por la salida predeterminada del sistema. Si se elige una salida concreta en “Salida”,
  la app mueve allí el sonido del Studio (necesita `pactl`, paquete pulseaudio-utils).
* El reloj del gemelo avanza mientras la página está visible o algo está sonando; si no, descansa (ahorra tiempo de cálculo).

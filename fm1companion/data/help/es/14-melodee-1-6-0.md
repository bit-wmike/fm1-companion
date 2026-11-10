# Melodee 1.6.0

Melodee 1.6.0 es la versión final (2 de noviembre de 2026) tras ocho versiones preliminares (1.6.0-rc.1 a rc.8). Qué cambia
para ti en la suite y a qué prestar atención al actualizar.

## Actualizar en tres pasos

1. **Haz una copia de seguridad mientras aún funciona el firmware antiguo.** La app hace una por sí sola antes de la actualización – si vienes de
   Melodee 1.5.1 o anterior, es obligatoria. **Esta copia es tu camino de vuelta:** el firmware antiguo no puede leer
   lo que guarda 1.6, y rechaza las copias de 1.6.
2. En [Firmware+ › Instalar y versiones](app://page/firmware) elige *Melodee* y *v1.6.0* e instala.
   El plan muestra los avisos sobre 1.6 (copia de seguridad, volúmenes distintos).
3. **Sonidos de fábrica:** el cargador de actualización de 1.6.0 escribe por sí solo la fuente, los sonidos de fábrica y el programa Bluetooth. La
   app solo lo comprueba después. Si ya están, no escribe nada e informa: *“Recursos ya completos”*.
   Desde 1.5.1 o anterior esto no funciona la primera vez, porque ahí aún están guardados tus bancos de presets. El nuevo
   firmware los traslada al arrancar; después, la app escribe ella misma los sonidos de fábrica. A diferencia de The Firmware Hub,
   **no tienes que instalar dos veces**. Si el FM-1 sigue mostrando **“Setup unfinished”**: *Reescribir fuente y sonidos
   de fábrica* (misma página) o instala la misma versión una vez más.

## Los proyectos pueden sonar distinto

* **Las pistas CZ-1 suenan 6 dB más bajas** que antes.
* El **limitador** solo actúa a partir de −2 dBFS en lugar de −5,2 dBFS. Las mezclas fuertes conservan más nivel de pico (punto más alto
  aprox. −3,5 en lugar de −5,9 dBFS).
* **Dither TPDF** en todas partes, y MASTER actúa una sola vez al final sobre la mezcla terminada.

Así que revisa el balance de tus proyectos tras la actualización. Esto también se aplica si vienes de 1.6.0-rc.8.

## Grabación por USB

* La **grabación estéreo se toma ahora antes de MASTER**, como las cuatro pistas sueltas. El control de volumen del FM-1 ya no cambia
  el nivel de grabación; un ajuste bajo ya no significa una grabación baja. Puedes ver los niveles en la página
  [Grabación](app://page/recorder).
* El sonido **del ordenador al FM-1** se ajusta con **USB VOLUME** en el menú HOME (por defecto 50 % = el nivel antiguo con
  MASTER en el centro). MASTER ya no lo controla.

## Novedades de 1.6 – y cómo las trata la suite

| Novedad en Melodee 1.6 | En la suite |
|---|---|
| Canción con 64 secciones, silencios por pista, “Loop song” | La [Vista de proyecto](app://page/project_view) muestra las 64; la exportación MIDI sigue la canción |
| Pistas 1–4 con 16 patrones (5–8 mantienen 8), 32 ranuras de proyecto | Vista de proyecto, Proyectos, Copias de seguridad y Componer conocen los 16 y las 32 |
| Efectos punch temporizados (4 por sección, 27 efectos) | Se muestran en la tabla de la canción (solo lectura). Sonido exacto solo en el [Studio](app://page/studio) y en el FM-1, no en la exportación MIDI |
| Presets por bloque de canción y por patrón | Se muestran en la tabla de la canción y en el resumen de patrones (solo lectura). Primero suena el preset del bloque, luego el del patrón y luego el sonido de la pista. Exacto solo en el Studio |
| KIT CR78, KIT FM, edición por rangos, MIDI LEARN, MIDI Bluetooth | en el dispositivo. MIDI LEARN solo se mantiene hasta apagarlo |

Los efectos punch y los presets de canción se editan en el dispositivo. El editor web sigue mostrando solo
8 patrones en su vista de patrones y con su orden de proyecto solo accede a las ranuras 1–4. Si sustituye un arreglo completo,
borra los punch y los presets. Las copias de seguridad los conservan.

## Studio

Para 1.6.0 y 1.6.1 hay un [gemelo del Studio](help:13-studio) propio para cada una. Para dispositivos y copias de seguridad con
1.6.0-rc.7 o rc.8, sus gemelos siguen disponibles. El Studio toma la versión de tu FM-1 o de la copia. Si difiere, te lo dice.
Entre rc.x y 1.6.0 la diferencia es el volumen (ver arriba).

## Volver a una versión anterior

* El plan de instalación avisa cuando cambias de Melodee 1.6 a un Melodee anterior o a otro firmware.
  Tienes que confirmarlo expresamente.
* La app **no restaura una copia de 1.6 en un Melodee anterior**. En lugar de mostrar un error del dispositivo,
  te explica antes por qué. Usa la copia de seguridad de antes de la actualización a 1.6.
* Volver al firmware original borra la zona de recursos. En el camino de vuelta a Melodee, la app la escribe de nuevo.

## FM-1 con flash UCUN

Algunos FM-1 tienen un chip flash de UCUN (UC25HQ80, ID 0xB36014). Solo a partir de 1.6.0 Melodee guarda
correctamente en él. La **recuperación USB ROM (Rescate Nivel 2) aún no conoce esta flash** y se detiene sin
escribir. La app te lo indica entonces. Las instalaciones normales mediante Firmware+ no se ven afectadas.

## Melodee 1.6.1: reloj y sincronización

Melodee 1.6.1 (3 de noviembre de 2026) es una actualización normal desde 1.6.0: proyectos, presets, ajustes y copias de seguridad
siguen igual, 1.6.0 aún puede leerlos. Por eso el plan de instalación no muestra avisos adicionales. Si vienes
de una versión preliminar o de 1.5.1, se aplica todo lo anterior (copia de seguridad, volúmenes). Lo que cambia para ti:

* **El FM-1 ahora puede marcar el tempo.** Si GLO › GLOBAL › CLK está en **INT**, envía reloj MIDI por USB,
  USB HOST y Bluetooth, también parado, además de Start con PLAY y Stop con STOP. Una DAW o una caja de ritmos pueden
  seguirlo. Con **USB, TRS o BLE** el FM-1 sigue y no envía reloj. Por TRS no sale reloj.
* **¿Quién marca el ritmo?** Si la suite debe marcar el tempo ([MIDI › Reloj](app://page/midi)), pon CLK en el FM-1 en **USB**. Con
  INT el FM-1 no escucha el reloj de la app y envía el suyo; entonces van dos relojes. La página
  te lo recuerda. Si el FM-1 debe marcar el ritmo, pon CLK en **INT** y deja apagado el reloj de la app. La pestaña *Reloj* muestra entonces
  el tempo del FM-1 en “Reloj entrante (seguidor)”.
* **Canción desde una sección.** En la [Vista de proyecto](app://page/project_view), “Iniciar la canción en el propio FM-1”
  ahora también arranca desde una sección elegida, como PLAY en el cursor SONG. Con CLK INT, los dispositivos que van a la par
  reciben entonces **Song Position y Continue** y entran en el mismo punto. Song Position no tiene preroll: los dispositivos
  que primero tienen que buscar pueden perderse los primeros pulsos.
* **Song Position desde la app.** Con CLK USB el FM-1 sigue una Song Position. Por eso el reloj de la suite puede
  arrancar “desde el compás” (Song Position + Continue). El FM-1 pone cada patrón donde estaría si se tocara desde
  el principio. No toca en ese momento sus propias secciones SONG.
* **Reloj preciso.** INT funciona ahora con valores de nota exactos y ya no deriva. Los LFO sincronizados se ajustan
  a la canción.
* **Cargar un proyecto mantiene CLK.** CLK es ahora un ajuste del dispositivo: cargar un proyecto o una plantilla (también
  desde la suite, desde Proyectos o el Studio) ya no lo cambia. TUNE, SYSTEM MIDI y ROUT siguen viniendo
  del proyecto. Si una configuración dependía antes del CLK de un proyecto: ajusta CLK una vez en el FM-1.
* **Restaurar una copia de seguridad restaura CLK**, tal como estaba en el momento de la copia. Revisa CLK después si lo habías
  cambiado entretanto.

El gemelo del Studio 1.6.1 calcula con el mismo reloj que el dispositivo, pero no envía reloj por sí mismo.
Melodee solo ha comprobado hasta ahora la salida de reloj y Song Position con pruebas automatizadas, aún no con dispositivos
reales.

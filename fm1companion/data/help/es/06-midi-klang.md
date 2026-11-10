# MIDI y módulo de sonido

El FM-1 es un dispositivo MIDI USB. La suite lo aprovecha en varios lugares:

* **Herramientas MIDI** (página *MIDI*): conectar puertos, monitorizar, enviar reloj.
* **Módulo de sonido:** las pistas MIDI 5–8 de Melodee no producen sonido en el propio FM-1. Un módulo de sonido en
  el ordenador (p. ej. `fluidsynth` con el soundfont General MIDI) las hace audibles.
  Paquetes: `sudo apt install fluidsynth fluid-soundfont-gm`.
* **Archivo MIDI → patrón:** [Componer › Desde archivo MIDI](app://page/workshop) convierte un archivo MIDI en un patrón para una pista.

Si la app no encuentra MIDI, revisa la línea “MIDI” en la [Comprobación del sistema](app://action/syscheck) – normalmente
falta `python3-rtmidi`.

## Escuchar proyectos y patrones

En **Proyectos › Ver y exportar** y en **Componer** hay una barra de reproducción (Espacio inicia y detiene):

* **En el FM-1:** las notas van por MIDI al FM-1 activo, que las toca con **los sonidos ajustados en ese momento
  en sus pistas** – tal como suena. Para ello la app lee el ajuste
  *GLO › SYSTEM › ROUT* (qué canal MIDI toca qué pista). No se guarda nada. Si una pista
  está armada para grabar (REC), la reproducción no se inicia, para que el FM-1 no grabe nada.
  Si los sonidos del proyecto difieren de los del dispositivo, la app te lo indica.
* **Iniciar la canción en el propio FM-1:** con la música en curso que se acaba de leer, el FM-1 toca su canción él mismo.
* **En el ordenador (aproximación):** sin FM-1, la app genera una vista previa. Las pistas FM6 suenan con el patch real
  del proyecto, los demás motores solo de forma parecida.

**¿No hay sonido?** Con el botón **“Salida: …”** eliges adónde va el sonido en el ordenador. Si el FM-1 está
conectado como dispositivo de audio USB, a menudo se convierte en la salida predeterminada – entonces todo el sonido acaba en el FM-1 en lugar
de en tus altavoces. La [Comprobación del sistema](app://action/syscheck) muestra en la línea “Vista previa” adónde va el sonido en este momento.

**Deshacer:** en Componer, **Ctrl+Z** deshace cada cambio, **Ctrl+Shift+Z** o **Ctrl+Y**
lo rehace. “Deshacer en el FM-1” es otra cosa: vuelve a escribir en el dispositivo el estado anterior a la última
escritura.

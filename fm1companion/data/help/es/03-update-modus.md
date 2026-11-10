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

## Cuando no funciona nada

Si el ordenador muestra una unidad **“WL82 UBOOT1.00”** y la pantalla sigue negra, el chip está en su
modo de recuperación integrado. Entonces ayuda la página [Rescate](app://page/recovery), **Nivel 2**: te guía
paso a paso por el MvaveFM1Unbricker incluido (con una doble copia de seguridad obligatoria de la flash).
Encontrarás información de fondo en la sección “Firmware update and recovery” de
[awesome-fm-1](https://github.com/cicloid/awesome-fm-1).

Lo que el Nivel 2 comprueba antes de escribir – en la copia de seguridad leída en la **misma** ejecución:

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

## Si el FM-1 no aparece como “WL82 UBOOT1.00”

El chip solo muestra la unidad cuando ha caído en el modo de arranque ROM. En este orden (según la
solución de problemas de MvaveFM1Unbricker), con el FM-1 conectado directamente al ordenador:

1. **Déjalo encendido dos minutos.** Los firmwares Felucca tienen un watchdog y una protección de arranque: tras dos
   arranques fallidos seguidos, el FM-1 pasa por sí solo al modo de arranque ROM. Luego vuelve a comprobarlo.
2. Mantén pulsadas **OCT− y OCT+** a la vez, enciende y sigue pulsando 10 segundos (modo de actualización, si el firmware llega
   hasta ahí; en Melodee es la calibración).
3. **Tres reinicios rápidos:** encender, esperar 10 s, apagar; encender, 10 s, apagar; encender. Si cada arranque falla, el
   tercero acaba en modo de arranque ROM. (Así es también como un FM-1 puede llegar ahí por accidente.)
4. Si nada de esto ayuda, solo queda el hardware: [Rescate](app://page/recovery), **Nivel 3** (FM-1 Transporter).

## Causas típicas de interrupciones

* Cable USB flojo o demasiado largo, hub USB sin alimentación propia
* Subtensión en la Raspberry Pi – compruébalo con la [Comprobación del sistema](app://action/syscheck)
* Otro programa (editor en el navegador, DAW) se comunica con el FM-1 al mismo tiempo

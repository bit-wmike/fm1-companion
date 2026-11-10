# V16 – aviso

M-VAVE ofrece para descargar el firmware de fábrica **V16** (archivo `FM-1.fwsc`, identidad `FM-1_016`): ocho voces,
patrones de 64 pasos, 128 voces de fábrica en formato DX7. En la app, V16 es **experimental – solo con copia de seguridad**.

## Lo esencial en tres frases

* V16 organiza la memoria del FM-1 **de otra forma** que V15 y todos los firmwares de la comunidad (Melodee, SLOOP, Skiff, JIANT).
  Al cambiar, se pierden las canciones y los presets del dispositivo – por eso la copia de seguridad es obligatoria.
* Desde Melodee, a V16 solo se llega **pasando por la V15 original**: Melodee comprueba primero la cabecera de arranque y, si no, rechazaría V16 con
  “FAILED -5”. La app hace este paso intermedio automáticamente.
* El **camino de vuelta** (V16 → V15 → Melodee) **aún no se ha confirmado** con una V16 real. Por eso: no lo pruebes en tu
  FM-1 principal, sino – si tienes uno – en un segundo que pueda quedar fuera de servicio unos días.

## Qué es seguro y qué no

Para cada afirmación, la app indica de dónde procede:

| Afirmación | Fuente |
|---|---|
| Al instalar, V16 escribe 0x04000–0xA8FFF, V15 y Melodee solo hasta 0x92FFF. | de los paquetes; para V15 registrado en hardware real, para V16 deducido |
| El cargador de actualización es idéntico byte a byte en V13, V14, V15 y V16. | de los paquetes |
| Durante una actualización normal a V15 no se solicitó la cabecera de arranque (0x0–0x3FFF). | registro en hardware real – la cabecera del dispositivo y la del paquete eran idénticas |
| Si el cargador de actualización escribe también la cabecera con V16 (cabecera de arranque distinta). | **abierto** – el código fuente de Melodee lo da por hecho, el registro no puede descartarlo (lo aclara el volcado A ↔ B) |
| Un FM-1 que se identificaba como versión 16 pudo volver a V15 con una actualización normal. | registro – pero era un derivado de V15, no una V16 real |
| En el primer arranque, V16 formatea 0xBA000–0xC9FFF, 0xDA000–0xE2FFF y 0xED000–0xEFFFF. | solo observado en el emulador |
| Los restos de V16 en 0x93000–0xA8FFF no molestan a Melodee. | en el emulador con Melodee 1.5.1; para 1.6.0-rc.7 solo según el código fuente (los sectores sin un registro válido de Melodee cuentan como libres y se borran antes de escribir), no en el dispositivo |
| Si el cargador lo borra todo antes de escribir o sector por sector. | abierto (la estructura del cargador sugiere un borrado previo) |

## Cómo funciona en la app

* **Ida:** [Firmware+ › Instalar y versiones](app://page/firmware) › “Volver al original …” › V16. El plan muestra: copia de seguridad → V15 original
  → V16 → comprobar identidad. Mientras el FM-1 escribe, aparece en rojo **“No desconectes ni apagues ahora.”**, y la cancelación queda
  bloqueada. Al final la app dice qué ha comprobado (suma de verificación, identidad `FM-1_016`) y qué no (cada byte –
  eso solo es posible con un volcado).
* **Vuelta:** si V16 está en marcha, Firmware+ › “Instalar y versiones” muestra “Camino de vuelta desde V16 …”. La app instala primero V15, luego tu
  firmware de destino y, en el caso de Melodee, después la fuente y los sonidos de fábrica (recursos). Luego restaura tu copia de seguridad.
* **Comprobar:** [Rescate](app://page/recovery) › “¿Está limpio mi FM-1?”.

## ¿Está limpio mi FM-1?

* **Comprobación rápida (solo lectura, sin riesgo):** identidad, versión y, en Melodee a partir de 1.6, el estado de los recursos –
  el firmware recalcula por sí mismo la cabecera y la suma de verificación. **No** examina el resto de la memoria.
* **Comprobación del volcado:** una copia de seguridad completa (1 MiB) se compara sector por sector con todos los paquetes conocidos:
  cabecera de arranque, firmware, una V16 truncada, restos de V16, recursos de Melodee. Resultado en forma de semáforo.
  Obtienes el volcado en modo de arranque ROM (“Leer y comprobar en modo de arranque ROM …”, solo lectura) o como archivo.
* **Limpiar restos de V16** (modo experto de la tarjeta, modo de arranque ROM, **sin probar en un dispositivo real**): solo borra
  sectores de 4 KiB en 0x93000–0xA8FFF que, según una doble copia de seguridad recién leída, aún contienen exactamente V16 – y
  solo si la flash contiene, byte a byte, un firmware conocido con diseño de memoria de V15 (nunca con V16). Procedimiento: primero leer y
  planificar (no se borra nada), luego, tras tu confirmación (“CLEAN UP”), volver a hacer copia, borrar sector por sector
  y verificar, y al final comparar toda la flash. Si algo se interrumpe, un registro de operaciones recuerda el estado; el mismo botón
  lo reanuda. Se supone (sin comprobarlo) que el chip borra exactamente 4 KiB con la orden “borrar sector” – la
  app comprueba entonces cada bloque de 64 KiB y se detiene de inmediato si falta más. Según todo lo conocido hasta ahora
  (emulador, código fuente), los restos son inofensivos: dejarlos donde están también está bien.
  Para practicar: marca “Simulación” en el Nivel 2 – entonces todo se ejecuta contra la flash de práctica del unbricker.

## Plan de prueba para tu primera prueba real con V16 (preferiblemente con un FM-1 prescindible)

Cuatro copias de seguridad completas, cada una leída en modo de arranque ROM y comprobada con “¿Está limpio mi FM-1?”:

1. **Volcado A** – antes de V16 (Melodee en marcha, copia de seguridad hecha).
2. **Volcado B** – justo después de la actualización a V16. Normalmente V16 arranca de inmediato; entonces B muestra también el
   formateo del primer arranque. Compara A ↔ B: ¿se escribió de verdad hasta 0xA8FFF, se mantuvo igual la cabecera de arranque
   (o contiene después la cabecera de V16)?
   ¿Qué cambió en 0xE0000–0xE4FFF (búfer del cargador)?
3. **Volcado C** – tras el camino de vuelta a V15. ¿Siguen ahí los restos de V16?
4. **Volcado D** – tras Melodee. ¿Funciona Melodee 1.6.0-rc.7 con los restos? ¿Están todos los sonidos?
5. Opcional **Volcado E** – tras “Limpiar restos de V16”: ¿solo están vacíos los sectores con restos y todo lo demás igual que en D?

Por favor, **no** pruebes actualizaciones interrumpidas a propósito – ni siquiera en un FM-1 prescindible.

## Si el camino de vuelta no funciona

* Primero, el **modo de actualización** normal (mantén OCT− al encender, consulta “Modo de actualización y rescate”).
* Si el FM-1 ya no acepta nada ahí o se queda a oscuras: [Rescate › Nivel 2](app://page/recovery)
  (modo de arranque ROM “WL82 UBOOT1.00” con el unbricker integrado). Escribe la V15 original y se niega
  a escribir V16 (solo escribe hasta 0x92FFF – V16 quedaría truncada). Aún no lo hemos probado con
  V16 en el dispositivo.
* Si, tras V16, la **cabecera de arranque de V16** está en el dispositivo (la copia de seguridad lo muestra), el Nivel 2 **no** escribe V15:
  no está claro si V15 arranca bajo esta cabecera. En ese caso usa [M-Flasher](https://github.com/jvitkauskas/m-flasher) o
  `fm1_rescue.py` de [felucca-bsp](https://github.com/hugelton/felucca-bsp) (elige V15 o V16 según
  la cabecera). “Reparar ajustes” también está bloqueado con el diseño de memoria de V16 – ahí, 0x93000–0xA8FFF aún contiene
  firmware.

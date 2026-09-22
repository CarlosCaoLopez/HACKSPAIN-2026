# Ingeniería
Nuestra primitiva principal ha sido lograr el determinismo dónde no nos podemos permitir las alucinaciones.
 
Por ello, hemos cogido el SOTA matemático de como se gestionan emergencias/crisis humanitarias y hemos construído hacia atrás. Es decir, partiendo de que estas situaciones se modelan matemáticamente (policy). Así, el llm lo único que hace es definir estas ecuaciones que después se pasan por el solver. Esto se hace así para evitar que el llm decida asignar todas los recursos como ambulancias/camniones de bomberos a un punto dejando el resto sin cubrir ante emergecias que surjan. Así conseguimos nuestra primera pieza de determinismo.

En cuanto a nuestros voice agents, hemos investigado cómo opera el 911. Su misión es recabar la mayor información posible en el mínimo timepo y para ello cuentan con un guión para cada tipo de emergencia, con la orden de pasar a la siguiente pregunta si no consiguen la respuesta a una. Nuestros voice agents replican exacamente esto. Para evitar las alucinaciones, el texto recogido se pasa por jev, el cual se encarga de recoger/clasificar los "hechos" de esta llamada, eliminando las alucinaciones de raíz.

Lo podríamos resumir en un bucle de:
1. Capta información de llamadas y entorno (imagees satelitales etc)
2. Recupera los hechos
3. Crea el modelo con las restricciones
4. Obtén una solución
5. Pide los recursos
6. Adapta el plan con la nueva información

El sistema es cuasi perfecto y es que, para serlo, faltaría cambiar el llm que está en medio del sistema agéntico por una instancia de jev lo cual garantiza que el tipo de restricción escogida sea siempre correcta y ver cuándo es necesario escalar las llamadas a una persona. 
Al final vemos que esta arquitectura, gracias a los voice agents, a la velocidad de jev y al determinismo intrínseco del sistema replica exactamente una vertical que actualmente hacen personas de forma más lenta, menos fiable y menos escalable.

# Demo



# Finalmente
Ya para acabar, hemos conseguido un par de LOI durante la hackathon: una del delegado territorial de la xunta en Pontevedra y del VP de wildfire mitigation en PG&E. Nos habría gustado poder contactar con un bombero para poder validar que entendemos la operativa de estas cosas. 
Somos Taiafox y aunque en el vídeo seamos solo 2 en el equipo somos 4. 


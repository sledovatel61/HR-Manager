/**
 * Автопрокрутка доски при перетаскивании карточки (block C).
 *
 * В воронке 11 колонок, в экран помещается несколько, поэтому перенос
 * требовал прокручиваться к каждой колонке по очереди. Указатель у края
 * доски теперь прокручивает её непрерывно, и одно перетаскивание проходит
 * всю воронку.
 */

export const EDGE_ZONE_PX = 90;
export const EDGE_SPEED_PX = 18;

/** -1 = влево, 1 = вправо, 0 = не прокручивать. */
export function edgeScrollDirection(
  clientX: number,
  rect: { left: number; right: number }
): -1 | 0 | 1 {
  // Доска, которая целиком помещается на экране, краёв не имеет.
  if (rect.right - rect.left <= EDGE_ZONE_PX * 2) return 0;
  if (clientX - rect.left < EDGE_ZONE_PX) return -1;
  if (rect.right - clientX < EDGE_ZONE_PX) return 1;
  return 0;
}

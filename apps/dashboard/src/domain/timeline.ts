import type { OrderedDashboardEvent } from "../contracts/generated";

export type MissionTimeline = readonly OrderedDashboardEvent[];

function meaningful(orderedEvent: OrderedDashboardEvent): boolean {
  return orderedEvent.event.eventClass !== "TELEMETRY";
}

function orderedUnique(events: readonly OrderedDashboardEvent[]): OrderedDashboardEvent[] {
  const ordinals = new Set<number>();
  return [...events]
    .sort((left, right) => left.auditOrdinal - right.auditOrdinal)
    .filter(({ auditOrdinal }) => {
      if (ordinals.has(auditOrdinal)) {
        return false;
      }
      ordinals.add(auditOrdinal);
      return true;
    });
}

export function replaceTimelineFromSnapshot(
  snapshotTimeline: readonly OrderedDashboardEvent[],
): MissionTimeline {
  return orderedUnique(snapshotTimeline.filter(meaningful));
}

export function appendMeaningfulTimelineEvent(
  timeline: MissionTimeline,
  orderedEvent: OrderedDashboardEvent,
): MissionTimeline {
  if (
    !meaningful(orderedEvent) ||
    timeline.some(({ auditOrdinal }) => auditOrdinal === orderedEvent.auditOrdinal)
  ) {
    return timeline;
  }
  return orderedUnique([...timeline, orderedEvent]);
}

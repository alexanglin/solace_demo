/** An untrusted serialized input delivered by any dashboard source adapter. */
export interface DashboardSourceInput {
  readonly channel: string;
  readonly lastEventId?: string;
  readonly name: string;
  readonly raw: string;
}

export type DashboardSourceConsumer = (input: DashboardSourceInput) => Promise<void>;

export interface DashboardSourceSubscription {
  dispose(): void;
}

/** The one source boundary implemented by live, replay, and test adapters. */
export interface DashboardEventSource {
  open(consumer: DashboardSourceConsumer): DashboardSourceSubscription;
}

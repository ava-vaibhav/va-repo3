import { durable } from '@versori/run';
import { getIssuersWebhook } from './workflows/get-issuers';

async function main(): Promise<void> {
  const mi = await durable.DurableInterpreter.newInstance();

  mi.register(getIssuersWebhook);

  await mi.start();
}

main().then().catch((err) => console.error('Failed to run main()', err));

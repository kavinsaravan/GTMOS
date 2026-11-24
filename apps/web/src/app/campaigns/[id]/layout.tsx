import { CampaignChrome } from "@/components/campaign-chrome";

export default async function CampaignLayout({ children, params }: LayoutProps<"/campaigns/[id]">) {
  const { id } = await params;
  return <CampaignChrome id={id}>{children}</CampaignChrome>;
}

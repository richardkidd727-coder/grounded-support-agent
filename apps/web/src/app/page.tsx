import { SupportApp } from "@/components/chat/support-app";

// The shell prerenders statically; the conversation is client state streamed from the API.
export default function Home() {
  return <SupportApp />;
}

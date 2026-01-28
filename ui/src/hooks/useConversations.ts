/**
 * React Query hooks for assistant conversation management
 */

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import * as api from '../lib/api'

/**
 * List all conversations for a project
 */
export function useConversations(projectName: string | null) {
  return useQuery({
    queryKey: ['conversations', projectName],
    queryFn: () => api.listAssistantConversations(projectName!),
    enabled: !!projectName,
    staleTime: 30000, // Cache for 30 seconds
  })
}

/**
 * Retrieve a single conversation and its messages for the given project and conversation ID.
 *
 * The query is enabled only when both `projectName` and `conversationId` are truthy, caches results for 30 seconds, and applies a retry policy that does not retry on "not found" (HTTP 404) errors and otherwise allows up to 3 attempts.
 *
 * @returns The React Query result containing the conversation and its messages, along with query metadata.
export function useConversation(projectName: string | null, conversationId: number | null) {
  return useQuery({
    queryKey: ['conversation', projectName, conversationId],
    queryFn: () => api.getAssistantConversation(projectName!, conversationId!),
    enabled: !!projectName && !!conversationId,
    staleTime: 30_000, // Cache for 30 seconds
    retry: (failureCount, error) => {
      // Don't retry on "not found" errors (404) - conversation doesn't exist
      if (error instanceof Error && (
        error.message.toLowerCase().includes('not found') ||
        error.message === 'HTTP 404'
      )) {
        return false
      }
      return failureCount < 3
    },
  })
}

/**
 * Delete a conversation
 */
export function useDeleteConversation(projectName: string) {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (conversationId: number) =>
      api.deleteAssistantConversation(projectName, conversationId),
    onSuccess: (_, deletedId) => {
      // Invalidate conversations list
      queryClient.invalidateQueries({ queryKey: ['conversations', projectName] })
      // Remove the specific conversation from cache
      queryClient.removeQueries({ queryKey: ['conversation', projectName, deletedId] })
    },
  })
}